# backend/handler.py

## 模块概述

`handler.py` 是后端主入口模块，负责同时启动和管理三个核心服务：

1. **面试 WebSocket 服务**（端口 8888）：实时语音面试主链路
2. **前端日志接收 HTTP 服务**（端口 8889）：接收前端日志用于排障
3. **管理后台 FastAPI 服务**（端口 8890）：提供管理接口

该模块还实现了面试会话的完整生命周期管理，包括准入控制、排队机制、持久化队列和多层日志架构。

## 核心职责

1. **服务启动与编排**：初始化并协调三个服务的启动
2. **准入控制**：限制并发面试数量，超出限制则进入排队
3. **会话管理**：管理面试会话从创建到结束的完整生命周期
4. **持久化队列**：异步持久化对话记录和音频，失败自动重试
5. **日志架构**：实现服务器日志、单场面试日志、前端日志的分层记录
6. **音频处理**：验证并累积候选人和机器人音频数据

## 服务架构

### 三服务并行架构

```
┌────────────────────────────────────────┐
│          handler.py (main)             │
├────────────────────────────────────────┤
│  ┌──────────────────────────────────┐  │
│  │  WebSocket Server (8888)         │  │ ← 语音面试主链路
│  │  - handler(websocket, path)      │  │
│  │  - AdmissionController           │  │
│  │  - VoiceBotService               │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  HTTP Log Server (8889)          │  │ ← 前端日志接收
│  │  - handle_frontend_log_request() │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  FastAPI Admin API (8890)        │  │ ← 管理后台 API
│  │  - create_admin_app()            │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  PersistenceQueue                │  │ ← 异步持久化
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

## 准入控制详解

### AdmissionController 类

负责限制并发面试数量，超出后自动排队：

```python
class AdmissionController:
    def __init__(self, max_active: int):
        self.max_active = max_active              # 最大并发数
        self.active_tokens = set()                # 当前活跃 token
        self.active_counts: Dict[str, int] = {}   # 每个 token 的并发连接数
        self.wait_queue: Deque[QueueWaiter] = deque()  # 等待队列
```

### 核心方法

#### acquire_or_enqueue()

尝试获取名额或进入排队：

```python
async def acquire_or_enqueue(token: str) -> Tuple[bool, Optional[QueueWaiter], bool]:
    # 返回: (是否立即准入, 等待者对象, 是否重复排队)

    # 情况 1：token 已在活跃集合中，允许同 token 多连接
    if token in self.active_tokens:
        self.active_counts[token] += 1
        return True, None, False

    # 情况 2：未满，直接准入
    if len(self.active_tokens) < self.max_active:
        self.active_tokens.add(token)
        self.active_counts[token] = 1
        return True, None, False

    # 情况 3：已在排队中，返回重复标记
    for waiter in self.wait_queue:
        if waiter.token == token:
            return False, waiter, True

    # 情况 4：加入排队
    waiter = QueueWaiter(
        token=token,
        admitted_event=asyncio.Event(),
        enqueued_at=monotonic(),
        active_snapshot=len(self.active_tokens),
        limit_snapshot=self.max_active,
    )
    self.wait_queue.append(waiter)
    return False, waiter, False
```

#### release()

释放名额并唤醒队首等待者：

```python
async def release(token: str) -> None:
    # 减少连接计数
    current = self.active_counts.get(token, 0)
    if current > 1:
        self.active_counts[token] = current - 1
        return

    # 完全释放 token
    self.active_counts.pop(token, None)
    self.active_tokens.discard(token)

    # 唤醒队首
    while self.wait_queue and len(self.active_tokens) < self.max_active:
        waiter = self.wait_queue.popleft()
        if waiter.admitted_event.is_set():
            continue
        self.active_tokens.add(waiter.token)
        self.active_counts[waiter.token] = 1
        waiter.admitted_event.set()  # 唤醒等待者
        break
```

### 排队流程

**等待心跳循环**：

```python
while True:
    # 检查是否已准入
    if waiter.admitted_event.is_set():
        break

    # 检查超时
    waited_seconds = monotonic() - queue_wait_start
    if waited_seconds >= QUEUE_WAIT_TIMEOUT_SECONDS:
        await ADMISSION.remove_waiter(token)
        await websocket.send(QueueTimeoutPayload(...))
        return

    # 心跳等待
    try:
        await asyncio.wait_for(
            waiter.admitted_event.wait(),
            timeout=QUEUE_HEARTBEAT_SECONDS
        )
    except asyncio.TimeoutError:
        # 发送位置更新
        position, active, limit = await ADMISSION.snapshot(token)
        await websocket.send(QueueUpdatePayload(position, active, limit))
```

### 配置参数

| 参数 | 环境变量 | 默认值 | 说明 |
|-----|---------|--------|------|
| 最大并发数 | `MAX_ACTIVE_INTERVIEWS` | 5 | 同时进行的最大面试数 |
| 排队超时 | `QUEUE_WAIT_TIMEOUT_SECONDS` | 1800 (30 分钟) | 排队最长等待时间 |
| 心跳间隔 | `QUEUE_HEARTBEAT_SECONDS` | 5 | 排队位置更新间隔 |

### 排队事件序列

```
1. QueueEntered: 进入排队
   → position=3, active=5, limit=5

2. QueueUpdate (每 5 秒)
   → position=2, active=5, limit=5
   → position=1, active=4, limit=5

3. QueueAdmitted: 准入成功
   → active=5, limit=5
   → 开始面试流程
```

## 持久化队列详解

### PersistenceQueue 类

异步持久化队列，避免阻塞主链路：

```python
class PersistenceQueue:
    def __init__(self, logger: logging.Logger):
        self._queue: asyncio.Queue[Optional[PersistenceTask]] = asyncio.Queue(
            maxsize=PERSISTENCE_QUEUE_SIZE
        )
        self._worker_task: Optional[asyncio.Task] = None
```

### PersistenceTask 数据结构

```python
@dataclass
class PersistenceTask:
    token: str                          # 面试 token
    turns: list                         # 对话轮次列表
    candidate_pcm_bytes: bytes          # 候选人音频（PCM 格式）
    interviewer_encoded_bytes: bytes    # 机器人音频（编码格式）
    interview_completed: bool           # 是否正常完成
    candidate_audio_dropped_frames: int # 丢弃的音频帧数
    grace_seconds: int = 30             # 未完成面试的宽限期
    retries: int = 0                    # 当前重试次数
```

### 重试机制

**指数退避重试**：

```python
async def _process(self, task: PersistenceTask) -> None:
    try:
        # 持久化操作
        await asyncio.to_thread(save_interview_turns, task.token, task.turns)
        await asyncio.to_thread(persist_interview_audio, ...)
        if task.interview_completed:
            await asyncio.to_thread(mark_interview_completed, task.token)
    except Exception as persist_err:
        # 达到最大重试次数
        if task.retries >= PERSISTENCE_MAX_RETRIES:
            self.logger.error(f"failed token={task.token} retries={task.retries}")
            return

        # 指数退避
        task.retries += 1
        delay = PERSISTENCE_RETRY_BASE_SECONDS * (2 ** (task.retries - 1))
        await asyncio.sleep(delay)
        await self._queue.put(task)  # 重新入队
```

### 配置参数

| 参数 | 环境变量 | 默认值 | 说明 |
|-----|---------|--------|------|
| 队列大小 | `PERSISTENCE_QUEUE_SIZE` | 200 | 最大待处理任务数 |
| 最大重试 | `PERSISTENCE_MAX_RETRIES` | 3 | 失败后最多重试次数 |
| 基础延迟 | `PERSISTENCE_RETRY_BASE_SECONDS` | 0.3 | 重试基础延迟（秒） |
| 关闭超时 | `PERSISTENCE_SHUTDOWN_TIMEOUT_SECONDS` | 5 | 服务器关闭时等待队列清空的超时 |

**重试延迟计算**：
```
retry 1: 0.3s
retry 2: 0.6s
retry 3: 1.2s
```

## 日志架构详解

### 三层日志体系

```
┌─────────────────────────────────────────────┐
│  server_logger                              │ ← 服务器全局日志
│  backend/logs/backend-20250322-124530.log  │
└─────────────────────────────────────────────┘
         ↓ 面试开始时创建
┌─────────────────────────────────────────────┐
│  interview_logger (backend)                 │ ← 单场面试后端日志
│  backend/data/storage/interview_logs/       │
│    <token>/backend.log                      │
└─────────────────────────────────────────────┘
         ↓ 前端主动推送
┌─────────────────────────────────────────────┐
│  interview_logger (frontend)                │ ← 单场面试前端日志
│  backend/data/storage/interview_logs/       │
│    <token>/frontend.log                     │
└─────────────────────────────────────────────┘
```

### 服务器日志（server_logger）

**日志文件命名**：
```
backend/logs/backend-20250322-124530-p12345.log
                    日期   时间    PID
```

**记录内容**：
- 服务器启动/关闭
- 配置参数加载
- 自检结果
- 面试会话开始/结束
- 准入控制事件
- 持久化队列状态

**日志示例**：
```
2025-03-22 12:45:30 - INFO - event=server.startup.begin
2025-03-22 12:45:31 - INFO - event=server.config max_active=5 llm_limit=5
2025-03-22 12:45:35 - INFO - event=server.ws.ready url=ws://0.0.0.0:8888
2025-03-22 12:46:10 - INFO - event=interview.started token=abc123 remote=('192.168.1.100', 54321)
2025-03-22 12:56:30 - INFO - event=interview.closed token=abc123 status=completed
```

### 面试日志（interview_logger）

**文件结构**：
```
backend/data/storage/interview_logs/
  └── <token>/
      ├── backend.log    # 后端日志
      └── frontend.log   # 前端日志
```

**backend.log 内容**：
- 会话开始/结束
- ASR 识别结果
- Judge 决策
- LLM 响应
- TTS 播放
- 延迟指标

**frontend.log 内容**：
- 前端事件（用户交互、错误）
- 浏览器环境信息
- 网络状态
- 音频播放状态

### 异步日志 Handler

使用 `AsyncRotatingFileHandler` 避免 I/O 阻塞：

```python
handler = build_async_rotating_handler(
    log_path=log_path,
    max_bytes=10 * 1024 * 1024,      # 10MB
    backup_count=5,                   # 保留 5 个备份
    queue_size=10000,                 # 内存队列大小
    flush_interval_ms=200,            # 200ms 刷盘
    drop_policy="drop_oldest",        # 队列满时丢弃最旧日志
    close_timeout_seconds=5,          # 关闭超时
)
```

### Logger 缓存机制

**缓存策略**：
- 最大缓存数：`INTERVIEW_LOGGER_CACHE_MAX` (默认 1000)
- 空闲超时：`INTERVIEW_LOGGER_IDLE_SECONDS` (默认 900 秒 = 15 分钟)

**LRU 淘汰**：
```python
def _prune_interview_logger_cache() -> None:
    # 清理超时 logger
    stale_keys = [
        key for key, last_used in _INTERVIEW_LOGGER_LAST_USED.items()
        if now - last_used >= INTERVIEW_LOGGER_IDLE_SECONDS
    ]

    # 超出容量则 LRU 淘汰
    if len(_INTERVIEW_LOGGER_CACHE) > INTERVIEW_LOGGER_CACHE_MAX:
        ordered = sorted(_INTERVIEW_LOGGER_LAST_USED.items(), key=lambda x: x[1])
        overflow_count = len(_INTERVIEW_LOGGER_CACHE) - INTERVIEW_LOGGER_CACHE_MAX
        for key, _ in ordered[:overflow_count]:
            _release_interview_logger(*key)
```

## 音频处理

### 候选人音频提取

`_extract_pcm_audio()` 函数验证并提取 PCM 音频：

```python
def _extract_pcm_audio(raw_audio: bytes, log_fn) -> bytes:
    # 拒绝旧协议的嵌套帧
    if len(raw_audio) >= 8 and raw_audio[:4] == b'\x11\x20\x10\x00':
        log_fn("[InterviewPersist] drop candidate audio frame: legacy nested payload")
        return b""

    # 验证 PCM 格式（必须是偶数字节）
    if len(raw_audio) % 2 != 0:
        log_fn("[InterviewPersist] drop candidate audio frame: invalid pcm payload size")
        return b""

    return raw_audio
```

**累积逻辑**：
```python
async def async_gen(ws):
    async for m in ws:
        input_event = convert_binary_to_web_event(m)
        if input_event.event == USER_AUDIO and input_event.data:
            pcm_bytes = _extract_pcm_audio(input_event.data, interview_log)
            if pcm_bytes:
                candidate_audio.extend(pcm_bytes)
            else:
                candidate_audio_dropped_frames += 1
```

### 机器人音频累积

通过 Service 回调累积：

```python
def record_bot_audio(chunk: bytes):
    interviewer_audio_encoded.extend(chunk)

service = VoiceBotService(
    ...,
    on_bot_audio_chunk=record_bot_audio,
)
```

## WebSocket Handler 流程

### 完整处理流程

```python
async def handler(websocket, path):
    # 1. 验证 token
    token = parse_qs(urlparse(path).query).get("token", [None])[0]
    interview_data = await asyncio.to_thread(start_interview_session, token)
    if not interview_data:
        await websocket.send(BotErrorPayload(error=ErrorEvent("INVALID_TOKEN", ...)))
        return

    # 2. 准入控制
    admitted, waiter, duplicated = await ADMISSION.acquire_or_enqueue(token)
    if duplicated:
        await websocket.send(BotErrorPayload(error=ErrorEvent("TOKEN_ALREADY_WAITING", ...)))
        return

    # 3. 排队等待（如未准入）
    if not admitted:
        await websocket.send(QueueEnteredPayload(...))
        while True:
            await asyncio.wait_for(waiter.admitted_event.wait(), timeout=QUEUE_HEARTBEAT_SECONDS)
            # ... 发送心跳更新
        await websocket.send(QueueAdmittedPayload(...))

    # 4. 创建 VoiceBotService
    interview_logger = _get_interview_logger(token, "backend")
    service = VoiceBotService(...)
    await service.init()

    # 5. 启动事件循环
    outputs = service.handler_loop(async_gen(websocket))
    await fetch_output(websocket, outputs)

    # 6. 清理与持久化
    finally:
        await ADMISSION.release(token)
        await PERSISTENCE.submit(PersistenceTask(...))
        _release_interview_loggers_for_token(token)
```

## 前端日志接收

### HTTP 端点

```
POST /api/frontend-logs?token=<token>
Content-Type: application/json

["log line 1", "log line 2", ...]
```

### 请求验证

```python
async def handle_frontend_log_request(reader, writer):
    # 1. 解析请求行和头部
    method, path = _parse_request_line(request_line)
    token = parse_qs(urlparse(path).query).get("token")

    # 2. 验证 token
    if not await asyncio.to_thread(interview_exists, token):
        await _write_http_response(writer, _http_response(b"HTTP/1.1 400 Bad Request", ...))
        return

    # 3. 解析 body
    content_length = _parse_content_length(headers)
    body = await reader.readexactly(content_length)
    log_entries = _normalize_frontend_log_entries(json.loads(body))

    # 4. 写入日志
    frontend_logger = _get_interview_logger(token, "frontend")
    for entry in log_entries:
        frontend_logger.info(entry)
```

### 限制参数

| 参数 | 环境变量 | 默认值 | 说明 |
|-----|---------|--------|------|
| Body 最大大小 | `FRONTEND_LOG_MAX_BODY_BYTES` | 262144 (256KB) | 单次请求最大字节数 |
| 最大条目数 | `FRONTEND_LOG_MAX_ENTRIES` | 200 | 单次请求最多日志条数 |
| 单条最大长度 | `FRONTEND_LOG_MAX_ENTRY_CHARS` | 2000 | 单条日志最大字符数 |

## 配置项汇总

### 端口配置

```bash
WS_HOST=0.0.0.0
WS_PORT=8888          # WebSocket 服务
LOG_HOST=0.0.0.0
LOG_PORT=8889         # 前端日志接收
ADMIN_API_HOST=0.0.0.0
ADMIN_API_PORT=8890   # 管理后台 API
```

### 准入与排队

```bash
MAX_ACTIVE_INTERVIEWS=5             # 最大并发面试数
QUEUE_WAIT_TIMEOUT_SECONDS=1800    # 排队超时（30 分钟）
QUEUE_HEARTBEAT_SECONDS=5          # 排队心跳间隔
```

### 持久化

```bash
PERSISTENCE_QUEUE_SIZE=200          # 持久化队列大小
PERSISTENCE_MAX_RETRIES=3           # 最大重试次数
PERSISTENCE_RETRY_BASE_SECONDS=0.3  # 重试基础延迟
PERSISTENCE_SHUTDOWN_TIMEOUT_SECONDS=5  # 关闭超时
```

### 日志配置

```bash
ASYNC_LOG_QUEUE_SIZE=10000          # 异步日志队列大小
ASYNC_LOG_FLUSH_INTERVAL_MS=200     # 刷盘间隔（毫秒）
ASYNC_LOG_DROP_POLICY=drop_oldest   # 队列满时策略
ASYNC_LOG_CLOSE_TIMEOUT_SECONDS=5   # Logger 关闭超时
INTERVIEW_LOGGER_CACHE_MAX=1000     # Logger 缓存上限
INTERVIEW_LOGGER_IDLE_SECONDS=900   # Logger 空闲超时
```

## 故障排查

### 1. 服务无法启动

**检查自检结果**：
```bash
grep "startup_self_check" logs/backend-*.log
# 查看 LLM/ASR/TTS 自检是否通过
```

### 2. 排队一直不推进

**检查准入状态**：
```bash
# 查看当前活跃数
grep "QueueUpdate" logs/backend-*.log | tail -1

# 检查是否有会话未正常释放
grep "interview.closed" logs/backend-*.log | wc -l
grep "interview.started" logs/backend-*.log | wc -l
# 两者应相等
```

### 3. 音频或对话未持久化

**检查持久化队列**：
```bash
grep "interview_persist" logs/backend-*.log
# 查看成功/失败/重试记录
```

### 4. 日志丢失

**检查 AsyncLog 告警**：
```bash
grep "AsyncLog.*dropped" logs/backend-*.log
# 如有丢弃，增大 ASYNC_LOG_QUEUE_SIZE
```

## 性能优化

### 减少排队等待

```bash
# 增加并发数（需评估服务器资源）
MAX_ACTIVE_INTERVIEWS=10
```

### 加速持久化

```bash
# 增大队列，减少阻塞风险
PERSISTENCE_QUEUE_SIZE=500

# 减少重试次数，快速失败
PERSISTENCE_MAX_RETRIES=2
```

### 优化日志性能

```bash
# 增大内存队列，减少刷盘频率
ASYNC_LOG_QUEUE_SIZE=20000
ASYNC_LOG_FLUSH_INTERVAL_MS=500
```

## 相关测试

```bash
pytest backend/tests/test_handler_admission.py
pytest backend/tests/test_handler_persistence.py
pytest backend/tests/test_handler_logging.py
```
