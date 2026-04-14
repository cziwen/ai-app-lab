# 性能优化和监控指南

## 概述

本文档详细说明语音面试系统的性能优化策略、监控指标定义、Turn Trace 系统使用方法以及性能问题排查指南。

## 性能指标定义

### 延迟指标

| 指标名称 | 定义 | 目标值 | 关键程度 |
|---------|------|--------|----------|
| `judge_ms` | Judge LLM 评估耗时 | < 2000ms | 高 |
| `llm2_ttft_ms` | Interviewer LLM 首 token 延迟 | < 1000ms | 高 |
| `llm2_total_ms` | Interviewer LLM 完整生成耗时 | < 5000ms | 中 |
| `tts_init_ms` | TTS 客户端初始化耗时 | < 500ms | 中 |
| `rec_to_first_sentence_ms` | 识别完成到首句播放 (用户感知) | < 5000ms | **极高** |
| `rec_to_tts_done_ms` | 识别完成到回复播放完毕 | < 10000ms | 高 |
| `asr_silence_detect_ms` | ASR 静默检测时间 | 2000ms (固定) | 高 |

### 并发指标

| 指标名称 | 定义 | 默认值 | 说明 |
|---------|------|--------|------|
| `max_active_interviews` | 最大同时面试数 | 5 | 受 LLM 并发限制 |
| `llm_concurrent_requests` | LLM 最大并发请求数 | 5 | 信号量控制 |
| `queue_depth` | 排队深度 | 动态 | 超出限制的等待者数量 |
| `queue_wait_time_avg` | 平均排队等待时间 | < 300s | 用户体验指标 |

### 吞吐量指标

| 指标名称 | 定义 | 说明 |
|---------|------|------|
| `interviews_per_hour` | 每小时完成面试数 | 理论最大值: 60 * 60 / 平均时长 |
| `avg_interview_duration` | 平均面试时长 | 10-15 分钟 (3题 * 3-5分钟) |
| `turn_completion_rate` | 轮次完成率 | 成功完成的轮次 / 总轮次 |

### 资源指标

| 指标名称 | 定义 | 建议值 |
|---------|------|--------|
| `memory_per_interview` | 单场面试内存占用 | ~50MB |
| `cpu_usage_per_interview` | 单场面试 CPU 占用 | ~5% (4核 CPU) |
| `audio_buffer_size` | 音频缓冲区大小 | ~2MB (10分钟面试) |
| `log_size_per_interview` | 单场面试日志大小 | ~500KB |

## Turn Trace 系统

### 系统架构

Turn Trace 是嵌入在 VoiceBotService 中的轻量级性能追踪系统，通过记录关键时间戳来计算各阶段延迟。

```python
class VoiceBotService:
    # Turn Trace 相关字段
    current_turn_id: Optional[str] = None
    turn_timestamps_ms: Optional[Dict[str, int]] = None

    def _start_turn(self) -> None:
        """开始新轮次追踪"""
        self.current_turn_id = str(uuid.uuid4())
        self.turn_timestamps_ms = {}

    def _log_turn_event(self, event_name: str) -> None:
        """记录时间戳"""
        if not self.turn_timestamps_ms:
            return
        self.turn_timestamps_ms[event_name] = int(time.monotonic() * 1000)

    def _duration_ms(self, start_event: str, end_event: str) -> Optional[int]:
        """计算两个事件之间的耗时"""
        if not self.turn_timestamps_ms:
            return None
        start = self.turn_timestamps_ms.get(start_event)
        end = self.turn_timestamps_ms.get(end_event)
        if start and end:
            return max(0, end - start)
        return None

    def _end_turn(self) -> None:
        """结束轮次并记录指标"""
        self._log_turn_latency_breakdown(status="success")
        self.current_turn_id = None
        self.turn_timestamps_ms = None
```

### 关键时间戳定义

```python
# 1. ASR 识别阶段
"turn_recognized_emitted"        # ASR 识别完成，返回候选人文本

# 2. Judge LLM 阶段
"judge_start"                    # Judge 开始评估回答
"judge_end"                      # Judge 完成评估，返回 Decision

# 3. Interviewer LLM 阶段
"interviewer_llm_start"          # LLM2 开始生成对话
"interviewer_llm_first_token"    # LLM2 返回首个 token (TTFT)
"interviewer_llm_end"            # LLM2 完成生成

# 4. TTS 阶段
"tts_init_start"                 # TTS 客户端初始化开始
"tts_init_end"                   # TTS 客户端就绪
"tts_stream_start"               # TTS 流开始
"tts_first_sentence_start"       # 首句开始播放 (用户听到声音)
"tts_done"                       # TTS 完成，回复播放完毕
```

### 使用示例

#### 面试模式完整追踪

```python
async def _interview_handler_loop(self, inputs):
    while True:
        # 等待候选人回答
        asr_responses = await self.handle_input_event(inputs)
        async for asr_recognized in self.handle_asr_response(asr_responses):
            # ===== 开始追踪 =====
            self._start_turn()
            self.state = StateInProgress

            # 时间戳 1: 识别完成
            self._log_turn_event("turn_recognized_emitted")

            candidate_text = asr_recognized.sentence
            self.history_messages.append(ArkMessage(role="user", content=candidate_text))

            # ===== Judge LLM 阶段 =====
            self._log_turn_event("judge_start")
            answer_resp = await self.interview_flow.receive_candidate_answer(candidate_text)
            self._log_turn_event("judge_end")

            decision = answer_resp.decision

            # 检查是否结束
            if self.interview_flow.is_done:
                await self._send_scripted_text(wrap_text)
                self._emit_interview_completed()
                return

            # 生成下一步内容
            next_resp = await self.interview_flow.produce_interviewer_message()

            # ===== Interviewer LLM 阶段 =====
            interview_context = self._build_interview_context(
                decision, next_resp.interviewer_text, self.interview_flow.state
            )

            self._log_turn_event("interviewer_llm_start")

            # 流式调用 LLM2
            llm_stream = self.stream_interview_llm_chat(interview_context)

            # ===== TTS 阶段 =====
            async for payload in self.handle_tts_response(llm_stream):
                yield WebEvent.from_payload(payload)

            # ===== 结束追踪 =====
            self.state = StateIdle
            self._end_turn()
```

#### LLM2 首 token 追踪

```python
async def stream_interview_llm_chat(self, interview_context: str):
    first_token = True
    completion_buffer = ""

    async with llm_slot():
        async for delta_text in adapter.stream_text(
            model=self.llm2_endpoint_id,
            instructions=INTERVIEWER_SYSTEM_PROMPT,
            messages=[ArkMessage(role="user", content=interview_context)],
        ):
            # 记录首 token 时间
            if first_token and delta_text:
                self._log_turn_event("interviewer_llm_first_token")
                first_token = False

            completion_buffer += delta_text
            yield delta_text

        # 记录完成时间
        self._log_turn_event("interviewer_llm_end")
```

#### TTS 追踪

```python
async def handle_tts_response(self, llm_output: AsyncIterable[str]):
    if not self.tts_client.inited:
        self._log_turn_event("tts_init_start")
        await self.tts_client.init()
        self._log_turn_event("tts_init_end")

    first_sentence = True
    self._log_turn_event("tts_stream_start")

    async for tts_rsp in self.tts_client.tts(source=llm_output, include_transcript=True):
        if tts_rsp.event == EventTTSSentenceStart:
            if first_sentence:
                self._log_turn_event("tts_first_sentence_start")
                first_sentence = False
            yield TTSSentenceStartPayload(sentence=tts_rsp.transcript)

        elif tts_rsp.audio:
            buffer.extend(tts_rsp.audio)
            self._emit_bot_audio_chunk(tts_rsp.audio)

        if tts_rsp.event == EventSessionFinished:
            self._log_turn_event("tts_done")
            yield TTSDonePayload()
```

### 日志输出格式

```
[TurnTrace] turn_id=a1b2c3d4 judge_ms=1850 llm2_ttft_ms=950 llm2_total_ms=4200
            tts_init_ms=150 rec_to_first_sentence_ms=4800
            rec_to_tts_done_ms=9500 status=success
```

### 数据分析

#### 查询单场面试的所有轮次指标

```bash
token="INT-abc123"
grep "TurnTrace" backend/data/storage/interview_logs/$token/backend.log
```

#### 提取平均延迟

```bash
grep "TurnTrace" backend/data/storage/interview_logs/$token/backend.log | \
  awk '{for(i=1;i<=NF;i++) if($i~"rec_to_first_sentence_ms") print $i}' | \
  cut -d= -f2 | \
  awk '{sum+=$1; count++} END {print "Average:", sum/count "ms"}'
```

#### 识别慢轮次 (超过 8 秒)

```bash
grep "TurnTrace" backend/data/storage/interview_logs/$token/backend.log | \
  awk '{for(i=1;i<=NF;i++) if($i~"rec_to_first_sentence_ms") print $0}' | \
  awk '{for(i=1;i<=NF;i++) if($i~"rec_to_first_sentence_ms") val=$i}
       split(val,a,"="); if(a[2]>8000) print'
```

## 延迟优化策略

### 1. ASR 静默检测优化

**问题**: 静默检测时间固定为 2000ms，可能导致用户感知延迟

**当前实现**:
```python
ASR_SILENCE_THRESHOLD_MS = 2000  # 固定值
```

**优化策略**:

#### A. 动态静默检测
```python
# 根据上下文动态调整
if is_one_word_answer:  # 如 "是", "对"
    threshold = 1000ms
elif is_long_answer:    # 复杂回答
    threshold = 3000ms
else:
    threshold = 2000ms
```

#### B. VAD (Voice Activity Detection) 增强
```python
# 结合 ASR 置信度
if asr_confidence > 0.9 and silence_duration > 1500ms:
    finalize_early()
```

**效果**: 可减少 500-1000ms 延迟

### 2. LLM 并发控制优化

**问题**: LLM 并发限制过低导致请求排队

**当前配置**:
```bash
LLM_CONCURRENT_REQUESTS=5
MAX_ACTIVE_INTERVIEWS=5
```

**分析**: 单场面试最多需要 2 个 LLM 槽位 (Judge + Interviewer)，最坏情况下 5 场面试同时调用 Judge 会占满所有槽位。

**优化策略**:

#### A. 增加 LLM 并发数
```bash
# 保守配置 (每场 2 个槽位)
LLM_CONCURRENT_REQUESTS = MAX_ACTIVE_INTERVIEWS * 2

# 示例: 5 场面试
LLM_CONCURRENT_REQUESTS=10
```

#### B. 分离 Judge 和 Interviewer 限制
```python
# 创建独立的信号量
judge_semaphore = asyncio.Semaphore(5)
interviewer_semaphore = asyncio.Semaphore(10)

@asynccontextmanager
async def judge_slot():
    await judge_semaphore.acquire()
    try:
        yield
    finally:
        judge_semaphore.release()

@asynccontextmanager
async def interviewer_slot():
    await interviewer_semaphore.acquire()
    try:
        yield
    finally:
        interviewer_semaphore.release()
```

**效果**: 避免 Judge 和 Interviewer 互相阻塞，减少排队延迟

### 3. Judge LLM 加速

**问题**: Judge 评估耗时 1.5-3 秒

**优化策略**:

#### A. 简化 Prompt
```python
# 原 Prompt (冗长)
"你是面试评判专家，根据以下评分标准详细评估候选人回答质量，并生成决策..."

# 优化后 (精简)
"评估回答是否充分，输出: {coverage_score, need_follow_up, follow_up_question}"
```

#### B. 启用思维链加速
```python
# 对于简单问题禁用深度思考
LLM1_THINKING_TYPE=disabled       # 最快
LLM1_REASONING_EFFORT=minimal     # 最低推理强度
```

#### C. 缓存评估结果 (高级)
```python
# 对于相似回答，缓存 Judge 结果
cache_key = hash(f"{question_id}:{normalize(answer)}")
if cache_key in judge_cache:
    return judge_cache[cache_key]
```

**效果**: Judge 延迟可降至 800-1500ms

### 4. Interviewer LLM TTFT 优化

**问题**: LLM2 首 token 延迟影响用户感知

**优化策略**:

#### A. 预热 LLM 连接
```python
# 服务初始化时预热
async def init(self):
    # 发送空请求预热连接
    async with llm_slot():
        _ = await adapter.stream_text(
            model=self.llm2_endpoint_id,
            messages=[ArkMessage(role="user", content="Hi")],
        )
```

#### B. 减少 System Prompt 长度
```python
# 原 INTERVIEWER_SYSTEM_PROMPT (1000+ tokens)
# 优化后 (500 tokens)
INTERVIEWER_SYSTEM_PROMPT = """
你是专业 AI 面试官，根据评估结果生成自然对话。
规则:
1. 简洁回应
2. 自然过渡
3. 不重复问题
"""
```

#### C. 启用流式优先模式
```python
# 优先返回首 token，延后完整性校验
LLM2_THINKING_TYPE=disabled
```

**效果**: TTFT 可降至 500-800ms

### 5. TTS 延迟优化

**问题**: TTS 初始化和合成延迟

**优化策略**:

#### A. TTS 客户端池化
```python
# 创建连接池避免反复初始化
class TTSClientPool:
    def __init__(self, pool_size=3):
        self.pool = [AsyncTTSClient(...) for _ in range(pool_size)]
        self.available = asyncio.Queue()
        for client in self.pool:
            self.available.put_nowait(client)

    async def acquire(self):
        client = await self.available.get()
        if not client.inited:
            await client.init()
        return client

    async def release(self, client):
        await self.available.put(client)
```

#### B. 预初始化 TTS
```python
# 在 ASR 识别阶段提前初始化 TTS
async def handle_asr_response(self, asr_responses):
    # ASR 识别中，提前准备 TTS
    if not self.tts_client.inited:
        asyncio.create_task(self.tts_client.init())  # 非阻塞初始化
```

#### C. 调整 TTS 参数
```bash
# 降低质量以换取速度
TTS_SPEED_RATIO=1.2  # 加快 20%
TTS_SAMPLE_RATE=16000  # 降低采样率 (默认 24000)
```

**效果**: TTS 延迟可降至 100-300ms

### 6. 流水线并行化

**问题**: Judge → LLM2 → TTS 串行执行

**优化策略**:

#### A. Judge 和 TTS 初始化并行
```python
# 同时启动 Judge 和 TTS 准备
async def parallel_prepare():
    judge_task = asyncio.create_task(judge.decide(...))
    tts_init_task = asyncio.create_task(self.tts_client.init())

    decision = await judge_task
    await tts_init_task  # 确保 TTS 就绪

    # 此时 TTS 已初始化完成
    llm_stream = self.stream_interview_llm_chat(...)
    await self.handle_tts_response(llm_stream)
```

#### B. 投机执行 (高级)
```python
# 在 Judge 评估期间提前生成通用回应
generic_response_task = asyncio.create_task(
    self.generate_generic_response()
)

decision = await judge.decide(...)

if decision.need_follow_up:
    # 取消通用回应，生成追问
    generic_response_task.cancel()
    llm_stream = self.stream_interview_llm_chat(...)
else:
    # 复用通用回应
    llm_stream = await generic_response_task
```

**效果**: 可节省 500-1000ms

## 并发控制策略

### 准入控制配置

**配置原则**: 根据 LLM 服务端承载能力和服务器资源决定

| 场景 | 推荐配置 | 说明 |
|-----|---------|------|
| 开发测试 | `MAX_ACTIVE_INTERVIEWS=2` | 降低开销 |
| 小规模部署 (2C4G) | `MAX_ACTIVE_INTERVIEWS=3` | 保守配置 |
| 中规模部署 (4C8G) | `MAX_ACTIVE_INTERVIEWS=5` | 默认配置 |
| 大规模部署 (8C16G) | `MAX_ACTIVE_INTERVIEWS=10` | 需配合 LLM 扩容 |

**关联配置**:
```bash
# 示例: 10 场并发面试
MAX_ACTIVE_INTERVIEWS=10
LLM_CONCURRENT_REQUESTS=20   # 每场 2 个 LLM 调用
INTERVIEW_OCCUPANCY_TTL_SECONDS=30
INTERVIEW_OCCUPANCY_HEARTBEAT_SECONDS=10
```

### 占用模型优化

**问题**: 高并发下容易出现 `INTERVIEW_CAPACITY_FULL` 拒绝

**优化策略**:

#### A. 动态调整准入限制
```python
# 根据系统负载动态调整
def get_dynamic_limit():
    cpu_usage = psutil.cpu_percent()
    memory_usage = psutil.virtual_memory().percent

    if cpu_usage < 50 and memory_usage < 70:
        return 10  # 低负载，允许更多并发
    elif cpu_usage < 70 and memory_usage < 85:
        return 5   # 中负载，默认配置
    else:
        return 3   # 高负载，限制并发
```

#### B. 收紧占用 TTL 与续期策略
```python
def recommend_occupancy_window(latency_ms: int) -> tuple[int, int]:
    # return (ttl_seconds, heartbeat_seconds)
    if latency_ms < 2000:
        return (20, 8)
    if latency_ms < 5000:
        return (30, 10)
    return (45, 15)
```

#### C. 针对拒绝流量做削峰
```python
def should_temporarily_raise_capacity(
    capacity_full_rate: float,
    cpu_usage: float,
    llm_wait_ms_p95: float,
) -> bool:
    return capacity_full_rate > 0.1 and cpu_usage < 70 and llm_wait_ms_p95 < 1200
```

### LLM 限流策略

**配置建议**:

```bash
# 场景 1: LLM 服务端 QPS 限制为 10
LLM_CONCURRENT_REQUESTS=8  # 留 20% 余量

# 场景 2: 多个应用共享 LLM
LLM_CONCURRENT_REQUESTS=3  # 按比例分配

# 场景 3: LLM 服务端无限制
LLM_CONCURRENT_REQUESTS=20  # 仅受本地资源限制
```

**监控指标**:
```python
# 记录 LLM 槽位等待时间
start = time.monotonic()
async with llm_slot():
    wait_ms = (time.monotonic() - start) * 1000
    if wait_ms > 1000:
        logging.warning(f"LLM slot waited {wait_ms}ms")
```

## 监控与告警

### 关键指标监控

#### 1. 实时监控

**日志采样**:
```bash
# 每 10 秒采样一次关键指标
while true; do
    echo "=== $(date) ==="
    echo "Active interviews:"
    grep "interview.started" logs/backend-*.log | tail -5 | wc -l

    echo "Capacity full rejects (recent 5 lines):"
    grep "event=interview.rejected reason=capacity_full" logs/backend-*.log | tail -5 | wc -l

    echo "Recent latencies:"
    grep "TurnTrace" logs/backend-*.log | tail -5 | grep -oP 'rec_to_first_sentence_ms=\K\d+'

    sleep 10
done
```

#### 2. 性能指标聚合

**每日报告**:
```bash
#!/bin/bash
# performance_report.sh

LOG_DIR="backend/data/storage/interview_logs"
DATE=$(date +%Y-%m-%d)

echo "=== Performance Report $DATE ==="

# 总面试数
total=$(find $LOG_DIR -name "backend.log" -mtime -1 | wc -l)
echo "Total interviews: $total"

# 平均延迟
avg_latency=$(find $LOG_DIR -name "backend.log" -mtime -1 -exec grep "TurnTrace" {} \; | \
  grep -oP 'rec_to_first_sentence_ms=\K\d+' | \
  awk '{sum+=$1; count++} END {print sum/count}')
echo "Average latency: ${avg_latency}ms"

# 慢轮次数量 (>8s)
slow_turns=$(find $LOG_DIR -name "backend.log" -mtime -1 -exec grep "TurnTrace" {} \; | \
  grep -oP 'rec_to_first_sentence_ms=\K\d+' | \
  awk '$1>8000' | wc -l)
echo "Slow turns (>8s): $slow_turns"

# Judge 延迟 P50/P95
judge_p50=$(find $LOG_DIR -name "backend.log" -mtime -1 -exec grep "TurnTrace" {} \; | \
  grep -oP 'judge_ms=\K\d+' | \
  sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.5)]}')
judge_p95=$(find $LOG_DIR -name "backend.log" -mtime -1 -exec grep "TurnTrace" {} \; | \
  grep -oP 'judge_ms=\K\d+' | \
  sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.95)]}')
echo "Judge latency P50: ${judge_p50}ms, P95: ${judge_p95}ms"

# LLM2 TTFT P50/P95
ttft_p50=$(find $LOG_DIR -name "backend.log" -mtime -1 -exec grep "TurnTrace" {} \; | \
  grep -oP 'llm2_ttft_ms=\K\d+' | \
  sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.5)]}')
ttft_p95=$(find $LOG_DIR -name "backend.log" -mtime -1 -exec grep "TurnTrace" {} \; | \
  grep -oP 'llm2_ttft_ms=\K\d+' | \
  sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.95)]}')
echo "LLM2 TTFT P50: ${ttft_p50}ms, P95: ${ttft_p95}ms"
```

#### 3. 告警规则

**Prometheus 告警 (示例)**:
```yaml
groups:
  - name: interview_performance
    rules:
      - alert: HighLatency
        expr: interview_latency_ms_p95 > 8000
        for: 5m
        annotations:
          summary: "P95 latency exceeds 8s"

      - alert: HighCapacityRejectRate
        expr: rate(interview_capacity_full_total[5m]) > 0.1
        for: 3m
        annotations:
          summary: "Capacity full reject rate > 10%"

      - alert: LLMSlotStarvation
        expr: llm_slot_wait_time_ms_avg > 2000
        for: 2m
        annotations:
          summary: "LLM slots saturated"

      - alert: HighFailureRate
        expr: rate(interview_errors[5m]) > 0.1
        annotations:
          summary: "Error rate > 10%"
```

### 日志分析工具

#### 1. 提取性能指标脚本

```bash
#!/bin/bash
# extract_metrics.sh <token>

TOKEN=$1
LOG_PATH="backend/data/storage/interview_logs/$TOKEN/backend.log"

if [ ! -f "$LOG_PATH" ]; then
    echo "Log not found: $LOG_PATH"
    exit 1
fi

echo "=== Performance Metrics for $TOKEN ==="

# 轮次数量
turns=$(grep "TurnTrace" $LOG_PATH | wc -l)
echo "Total turns: $turns"

# 各阶段延迟统计
echo -e "\n=== Latency Breakdown ==="
echo "Judge LLM:"
grep "TurnTrace" $LOG_PATH | grep -oP 'judge_ms=\K\d+' | \
  awk '{sum+=$1; if(min==""|$1<min) min=$1; if($1>max) max=$1} END {print "  Min:", min "ms, Max:", max "ms, Avg:", sum/NR "ms"}'

echo "LLM2 TTFT:"
grep "TurnTrace" $LOG_PATH | grep -oP 'llm2_ttft_ms=\K\d+' | \
  awk '{sum+=$1; if(min==""|$1<min) min=$1; if($1>max) max=$1} END {print "  Min:", min "ms, Max:", max "ms, Avg:", sum/NR "ms"}'

echo "Total (rec to first sentence):"
grep "TurnTrace" $LOG_PATH | grep -oP 'rec_to_first_sentence_ms=\K\d+' | \
  awk '{sum+=$1; if(min==""|$1<min) min=$1; if($1>max) max=$1} END {print "  Min:", min "ms, Max:", max "ms, Avg:", sum/NR "ms"}'

# 异常轮次
echo -e "\n=== Slow Turns (>8s) ==="
grep "TurnTrace" $LOG_PATH | \
  awk '{for(i=1;i<=NF;i++) if($i~"rec_to_first_sentence_ms") val=$i; for(i=1;i<=NF;i++) if($i~"turn_id") tid=$i}
       split(val,a,"="); split(tid,b,"=");
       if(a[2]>8000) print "Turn", b[2], ":", a[2] "ms"'
```

#### 2. 慢查询分析

```python
# slow_turn_analysis.py
import re
import sys
from collections import defaultdict

def analyze_slow_turns(log_path, threshold_ms=8000):
    slow_turns = []

    with open(log_path) as f:
        for line in f:
            if "TurnTrace" not in line:
                continue

            turn_id = re.search(r'turn_id=(\S+)', line).group(1)
            rec_to_first = int(re.search(r'rec_to_first_sentence_ms=(\d+)', line).group(1))

            if rec_to_first > threshold_ms:
                judge_ms = int(re.search(r'judge_ms=(\d+)', line).group(1))
                llm2_ttft = int(re.search(r'llm2_ttft_ms=(\d+)', line).group(1))
                llm2_total = int(re.search(r'llm2_total_ms=(\d+)', line).group(1))

                slow_turns.append({
                    'turn_id': turn_id,
                    'total': rec_to_first,
                    'judge': judge_ms,
                    'llm2_ttft': llm2_ttft,
                    'llm2_total': llm2_total
                })

    # 统计慢原因
    bottlenecks = defaultdict(int)
    for turn in slow_turns:
        if turn['judge'] > 3000:
            bottlenecks['judge_slow'] += 1
        if turn['llm2_ttft'] > 2000:
            bottlenecks['llm2_ttft_slow'] += 1
        if turn['llm2_total'] > 8000:
            bottlenecks['llm2_generation_slow'] += 1

    print(f"Found {len(slow_turns)} slow turns (>{threshold_ms}ms)")
    print("\nBottleneck analysis:")
    for reason, count in bottlenecks.items():
        print(f"  {reason}: {count}")

if __name__ == "__main__":
    analyze_slow_turns(sys.argv[1])
```

## 性能问题排查

### 常见问题与解决方案

#### 1. 整体延迟高 (rec_to_first_sentence_ms > 8s)

**排查步骤**:

```bash
# 1. 检查各阶段耗时
token="INT-xxx"
grep "TurnTrace" backend/data/storage/interview_logs/$token/backend.log | tail -1

# 输出示例:
# judge_ms=3500 llm2_ttft_ms=2800 rec_to_first_sentence_ms=9200
```

**分析**:
- `judge_ms > 3000ms` → Judge LLM 慢 (参考"Judge LLM 加速")
- `llm2_ttft_ms > 2000ms` → LLM2 首 token 慢 (参考"Interviewer LLM TTFT 优化")
- 其他耗时正常 → 可能是网络延迟

**解决方案**:
```bash
# 临时降低思考强度
export LLM1_THINKING_TYPE=disabled
export LLM2_THINKING_TYPE=disabled
export LLM1_REASONING_EFFORT=minimal
export LLM2_REASONING_EFFORT=minimal

# 重启服务
docker compose restart backend
```

#### 2. LLM 槽位饱和

**症状**: 日志显示 `LLM slot waited XXXXms`

**排查**:
```bash
# 检查 LLM 并发配置
grep "llm_limit" logs/backend-*.log

# 统计等待时长
grep "LLM slot waited" logs/backend-*.log | \
  grep -oP 'waited \K\d+' | \
  awk '{sum+=$1; if($1>max) max=$1; count++} END {print "Max:", max "ms, Avg:", sum/count "ms"}'
```

**解决方案**:
```bash
# 增加 LLM 并发数
export LLM_CONCURRENT_REQUESTS=15
docker compose restart backend
```

#### 3. ASR 识别延迟

**症状**: 候选人说完话后等待 3-5 秒才收到反馈

**排查**:
```bash
# 检查 ASR 静默检测配置
grep "ASR_SILENCE" backend/handler.py
```

**分析**: 静默检测时间固定为 2000ms 是正常的，额外延迟可能来自网络或 ASR 服务端。

**解决方案**:
```bash
# 1. 检查网络延迟
ping openspeech.bytedance.com

# 2. 检查 ASR 服务端状态 (联系技术支持)

# 3. 尝试切换 ASR 区域 (如有备用)
export ASR_WS_URL=wss://openspeech-backup.bytedance.com/api/v3/sauc/bigmodel_async
```

#### 4. TTS 合成慢

**症状**: LLM2 已生成完整文本，但音频播放延迟

**排查**:
```bash
# 检查 TTS 初始化时间
grep "tts_init_ms" backend/data/storage/interview_logs/$token/backend.log
```

**分析**:
- `tts_init_ms > 1000ms` → TTS 初始化慢
- `tts_init_ms` 正常但总延迟高 → TTS 合成慢

**解决方案**:
```bash
# 1. 使用 TTS 客户端池化 (需修改代码)
# 2. 降低音质换取速度
export TTS_SPEED_RATIO=1.2
export TTS_SAMPLE_RATE=16000

# 3. 检查 TTS 服务端状态
```

#### 5. 内存泄漏或 OOM

**症状**: 服务运行一段时间后变慢或崩溃

**排查**:
```bash
# 查看容器内存使用
docker stats live_voice_call_backend

# 检查是否有未释放的 Logger
grep "interview_logger_cache" logs/backend-*.log | tail -10
```

**解决方案**:
```bash
# 1. 增加 Logger 淘汰频率
export INTERVIEW_LOGGER_IDLE_SECONDS=600  # 10 分钟

# 2. 限制 Logger 缓存大小
export INTERVIEW_LOGGER_CACHE_MAX=500

# 3. 重启服务
docker compose restart backend
```

## 性能基准测试

### 单机基准

**测试环境**: 4C8G, 5 并发面试

```bash
# 性能指标 (平均值)
judge_ms: 1850ms
llm2_ttft_ms: 950ms
rec_to_first_sentence_ms: 4500ms
rec_to_tts_done_ms: 9200ms

# 资源占用
CPU 使用率: 60%
内存使用: 3.5GB
网络带宽: 5Mbps (上传 + 下载)
```

### 压力测试

**工具**: Locust / K6

**测试脚本** (伪代码):
```python
import asyncio
import websockets

async def interview_session():
    uri = "ws://localhost:8888?token=test-token"
    async with websockets.connect(uri) as ws:
        # 接收 BotReady
        await ws.recv()

        # 模拟 10 轮对话
        for i in range(10):
            # 发送音频 (模拟 5 秒语音)
            for _ in range(50):
                await ws.send(pack_audio(random_pcm_bytes()))
                await asyncio.sleep(0.1)

            # 等待响应
            while True:
                msg = await ws.recv()
                if "TTSDone" in msg:
                    break

# 并发 50 个会话
await asyncio.gather(*[interview_session() for _ in range(50)])
```

## 相关文档

- [系统架构](ARCHITECTURE.md)
- [数据流向详解](DATA_FLOW.md)
- [故障排查手册](TROUBLESHOOTING.md)
