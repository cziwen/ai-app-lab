# backend/service.py

## 模块概述

`service.py` 是语音机器人服务的核心编排层，负责协调 ASR（语音识别）、LLM（语言模型）、TTS（语音合成）三大组件，实现实时语音对话和结构化面试两种模式。该模块采用**双 LLM 架构**（Judge LLM + Interviewer LLM）实现面试场景，通过异步事件循环和回调机制与上层 handler 进行数据交换。

## 核心职责

1. **组件管理**：初始化并管理 ASR、TTS、LLM 客户端的生命周期
2. **事件循环编排**：接收 WebSocket 输入事件，生成输出事件流
3. **双 LLM 协调**：在面试模式下协调 Judge LLM（评判）和 Interviewer LLM（对话生成）
4. **状态管理**：维护服务状态（Idle / InProgress）防止并发冲突
5. **音频流处理**：处理候选人音频输入和机器人音频输出
6. **回调通知**：通过回调函数向 handler 层传递对话内容和音频数据
7. **延迟追踪**：记录关键时间戳，用于性能分析

## 运行模式

### 普通对话模式（interview_mode=False）

```
用户音频 → ASR → LLM2 (VoiceBotPrompt) → TTS → 语音输出
```

- 单 LLM 模式，使用 `llm2_endpoint_id`
- 历史对话存储在 `history_messages`
- 适用于通用对话场景

### 面试模式（interview_mode=True）

```
开场 → 预设文本 → TTS
候选人回答 → ASR → Judge LLM (LLM1) → 评估决策
         → InterviewFlow 状态推进
         → Interviewer LLM (LLM2) → 自然语言生成 → TTS
```

**双 LLM 分工**：
- **LLM1（Judge）**：基于评分标准评估回答质量，决定是否追问
- **LLM2（Interviewer）**：根据 Judge 决策生成自然对话式回应

## 核心类：VoiceBotService

### 类定义与字段

```python
class VoiceBotService(BaseModel):
    # === 客户端实例 ===
    asr_client: Optional[SaucASRClient]        # ASR 客户端
    tts_client: Optional[AsyncTTSClient]       # TTS 客户端

    # === LLM 配置（双 LLM 架构）===
    ark_api_key: str                           # 方舟 API Key
    llm1_endpoint_id: str                      # Judge LLM 端点
    llm2_endpoint_id: str                      # Interviewer LLM 端点
    llm1_thinking_type: str = "disabled"       # Judge 思维链模式
    llm2_thinking_type: str = "disabled"       # Interviewer 思维链模式
    llm1_reasoning_effort: Optional[str]       # Judge 推理强度
    llm2_reasoning_effort: Optional[str]       # Interviewer 推理强度

    # === 服务状态 ===
    state: str = StateIdle                     # Idle / InProgress

    # === ASR/TTS 配置 ===
    asr_app_key: str
    asr_access_key: str
    asr_resource_id: str
    asr_ws_url: str
    tts_app_key: str
    tts_access_key: str
    tts_speaker: str = DEFAULT_SPEAKER

    # === 面试模式 ===
    interview_mode: bool = False
    interview_flow: Optional[InterviewFlow]
    interview_judge: Optional[InterviewJudge]
    interview_questions: Optional[List[Dict[str, Any]]]

    # === 回调函数 ===
    on_candidate_sentence: Optional[Callable[[str], None]]    # 候选人文本
    on_bot_sentence: Optional[Callable[[str], None]]          # 机器人文本
    on_bot_audio_chunk: Optional[Callable[[bytes], None]]     # 机器人音频块
    on_interview_completed: Optional[Callable[[], None]]      # 面试完成

    # === 对话历史 ===
    history_messages: List[ArkMessage] = []    # 对话历史（Ark 格式）

    # === ASR 状态追踪 ===
    asr_buffer: str = ""                       # ASR 累积文本
    asr_no_input_duration: int = 0            # 静音时长（毫秒）
    asr_last_growth_mono_ms: int = 0          # 最后一次文本增长时间戳
    asr_silence_tick_count: int = 0           # 静音检测 tick 计数
    asr_stream_reset_count: int = 0           # ASR 流重置次数
    asr_init_failure_streak: int = 0          # 连续初始化失败次数

    # === 延迟追踪 ===
    current_turn_id: Optional[str]            # 当前轮次 ID（UUID）
    turn_timestamps_ms: Optional[Dict[str, int]]  # 时间戳字典
```

## 双 LLM 架构详解

### LLM1：InterviewJudge（评判器）

**职责**：评估候选人回答质量，决定是否追问

**输入**：
- `question`：当前面试题
- `candidate_answer`：候选人回答
- `evidence`：评分标准（如 `scoring_boundary`）
- `follow_up_count`：已追问次数

**输出**（Decision 对象）：
```python
{
    "move_forward": bool,          # 是否进入下一题
    "need_follow_up": bool,        # 是否追问
    "follow_up_question": str,     # 追问内容
    "reason": str,                 # 决策理由
    "coverage_score": float        # 覆盖度得分（0~1）
}
```

**调用时机**：每次候选人回答后

**LLM 配置**：
- Endpoint: `llm1_endpoint_id`
- Thinking: `llm1_thinking_type` (disabled / enabled)
- Reasoning Effort: `llm1_reasoning_effort` (low / medium / high)

### LLM2：Interviewer LLM（对话生成器）

**职责**：基于 Judge 决策生成自然语言面试官回应

**输入**（通过 `_build_interview_context()` 构建）：
```python
{
    "[评估结果] 评判理由: ..."
    "[评估结果] 覆盖度得分: 0.85"
    "[指令] 候选人回答充分，请先简要肯定，然后自然过渡到下一个问题。"
    "[下一步内容] 请介绍一个你主导的项目..."
}
```

**输出**：流式文本（AsyncIterable[str]）

**调用时机**：Judge 决策后，生成自然对话

**LLM 配置**：
- Endpoint: `llm2_endpoint_id`
- System Prompt: `INTERVIEWER_SYSTEM_PROMPT`（定义在 `prompt.py`）
- Thinking: `llm2_thinking_type`
- Reasoning Effort: `llm2_reasoning_effort`

### LLM 调用流程对比

| 步骤 | LLM1 (Judge) | LLM2 (Interviewer) |
|-----|-------------|-------------------|
| 触发点 | `flow.receive_candidate_answer()` | `stream_interview_llm_chat()` |
| 输入格式 | JSON payload (question + answer + evidence) | Interview context (decision + next_question) |
| 输出格式 | 完整 JSON 对象 | 流式文本 |
| 历史记录 | 不影响 `history_messages` | 输出追加到 `history_messages` |
| 并发控制 | `async with llm_slot()` | `async with llm_slot()` |

## 事件循环详解

### 主循环：handler_loop()

```python
async def handler_loop(self, inputs: AsyncIterable[WebEvent]) -> AsyncIterable[WebEvent]:
```

**流程分支**：

**面试模式**：
```
1. 调用 _interview_handler_loop(inputs)
2. 返回面试专用事件流
```

**对话模式**：
```
1. send_greeting() 发送欢迎语
2. while True:
     a. handle_input_event(inputs) → ASR 流
     b. handle_asr_response() → 识别文本
     c. stream_llm_chat() → LLM 响应
     d. handle_tts_response() → 语音输出
```

### 面试模式主循环：_interview_handler_loop()

**阶段 1：开场（无 LLM）**
```python
# 发送开场白
intro_resp = await flow.produce_interviewer_message()  # INTRO -> ASK_QUESTION
await self._send_scripted_text(intro_resp.interviewer_text)

# 发送第一题
q1_resp = await flow.produce_interviewer_message()  # ASK_QUESTION -> WAIT_ANSWER
await self._send_scripted_text(q1_resp.interviewer_text)
```

**阶段 2：对话循环（双 LLM）**
```python
while True:
    # 1. 等待候选人语音输入
    asr_responses = await self.handle_input_event(inputs)
    async for asr_recognized in self.handle_asr_response(asr_responses):
        self.state = StateInProgress
        self._start_turn()  # 开始新轮次

        # 2. 记录候选人回答
        candidate_text = asr_recognized.sentence
        self.history_messages.append(ArkMessage(role="user", content=candidate_text))

        # 3. LLM1 (Judge) 评估回答
        self._log_turn_event("judge_start")
        answer_resp = await flow.receive_candidate_answer(candidate_text)
        self._log_turn_event("judge_end")
        decision = answer_resp.decision

        # 4. 检查是否结束
        if flow.is_done:
            await self._send_scripted_text(wrap_text)
            return

        # 5. 生成下一步内容（主问题或追问）
        next_resp = await flow.produce_interviewer_message()

        # 6. LLM2 (Interviewer) 生成自然语言
        interview_context = self._build_interview_context(
            decision, next_resp.interviewer_text, flow.state
        )
        self._log_turn_event("interviewer_llm_start")
        llm_stream = self.stream_interview_llm_chat(interview_context)

        # 7. TTS 播放
        async for payload in self.handle_tts_response(llm_stream):
            yield WebEvent.from_payload(payload)

        self.state = StateIdle
        self._end_turn()
```

### ASR 处理流程

#### handle_input_event()

接收 WebSocket 输入事件，转换为 ASR 字节流：

```python
async def handle_input_event(inputs: AsyncIterable[WebEvent]) -> AsyncIterable[SaucASRFullServerResponse]:
    # 1. 检查服务状态（只有 Idle 才接受输入）
    if self.state != StateIdle:
        continue

    # 2. 确保 ASR 客户端就绪
    if not self.asr_client.inited:
        ok = await self._ensure_asr_ready()
        if not ok:
            self.asr_init_failure_streak += 1
            if self.asr_init_failure_streak >= ASR_INIT_FATAL_FAILURE_STREAK:
                raise ASRInitUnavailableError()

    # 3. 处理输入事件
    if input_event.event == BOT_UPDATE_CONFIG:
        self.tts_speaker = input_event.payload.speaker
    elif input_event.event == USER_AUDIO:
        yield input_event.data  # 传递音频字节

    # 4. 启动 ASR 流
    async for asr_rsp in self.asr_client.stream_asr(async_gen()):
        yield asr_rsp
```

#### handle_asr_response()

处理 ASR 响应，累积识别结果并检测静音：

```python
async def handle_asr_response(asr_responses) -> AsyncIterable[SentenceRecognizedPayload]:
    while True:
        # 1. 检查静音超时（ASRInterval = 2000ms）
        pending_finalized = await self._finalize_asr_turn_if_silent()
        if pending_finalized:
            yield pending_finalized
            continue

        # 2. 等待 ASR 响应（带超时轮询）
        try:
            response = await asyncio.wait_for(
                next_asr_task, timeout=ASR_POLL_INTERVAL_SECONDS
            )
        except asyncio.TimeoutError:
            continue  # 继续检测静音

        # 3. 累积识别文本
        if len(response.result.text) > len(self.asr_buffer):
            self.asr_buffer = response.result.text
            self.asr_last_growth_mono_ms = self._mono_ms()
```

#### 跨题尾包隔离（连接代次守卫）

为避免「第 N 题尾包」在第 N+1 题被错误消费，`VoiceBotService` 在 ASR 处理链中引入了连接代次守卫（不改 WebSocket 协议，不改 InterviewFlow 主状态机）：

- `asr_stale_connect_id`：记录上一轮 finalize 时的 ASR 连接 ID（旧连接）。
- `asr_drop_stale_packets`：是否启用旧连接丢包守卫。

工作方式：

1. 在 `_finalize_asr_turn_if_silent()` 中，`close()` 前记录 `asr_client.connect_id` 到 `asr_stale_connect_id`，并启用 `asr_drop_stale_packets`。
2. 在 `handle_asr_response()` 中，每个包先检查 `response.stream_connect_id`：
   - 若守卫开启且 `stream_connect_id == asr_stale_connect_id`：直接丢弃（上一题尾包）。
   - 若守卫开启且 `stream_connect_id != asr_stale_connect_id`：清守卫并正常处理（进入新题连接）。
   - 若 `stream_connect_id` 为空：走兼容路径，不强制丢弃，避免异常环境卡死。

关键日志：

- `ASR_STALE_GUARD_ENABLED`：finalize 后启用守卫。
- `ASR_STALE_PACKET_DROPPED`：丢弃旧连接包。
- `ASR_STALE_GUARD_CLEARED`：收到新连接包后清守卫。

**静音检测逻辑**（`_finalize_asr_turn_if_silent()`）：

```python
silence_ms = now_ms - self.asr_last_growth_mono_ms
if silence_ms > ASRInterval:  # 2000ms
    sentence = self.asr_buffer
    self._reset_asr_buffer_state()
    await self.asr_client.close()
    return SentenceRecognizedPayload(sentence=sentence)
```

### TTS 处理流程

#### handle_tts_response()

将 LLM 输出流式转换为 TTS 音频：

```python
async def handle_tts_response(llm_output: AsyncIterable[str]) -> AsyncIterable[...]:
    # 1. 确保 TTS 客户端就绪
    if not self.tts_client.inited:
        await self.tts_client.init()

    # 2. 流式调用 TTS
    async for tts_rsp in self.tts_client.tts(source=llm_output, include_transcript=True):
        # 3. 分发事件
        if tts_rsp.event == EventTTSSentenceStart:
            yield TTSSentenceStartPayload(sentence=tts_rsp.transcript)
        elif tts_rsp.event == EventTTSSentenceEnd:
            yield TTSSentenceEndPayload(data=buffer)
        elif tts_rsp.audio:
            buffer.extend(tts_rsp.audio)
            self._emit_bot_audio_chunk(tts_rsp.audio)  # 回调

        if tts_rsp.event == EventSessionFinished:
            yield TTSDonePayload()
            await self.tts_client.close()
```

## 回调系统详解

### 回调函数类型

Service 提供 4 个可选回调，由 handler 层注册：

```python
service = VoiceBotService(
    ...,
    on_candidate_sentence=lambda text: record_turn("candidate", text),
    on_bot_sentence=lambda text: record_turn("interviewer", text),
    on_bot_audio_chunk=record_bot_audio,
    on_interview_completed=on_interview_completed,
)
```

### 回调触发时机

#### 1. on_candidate_sentence(text: str)

**触发点**：候选人语音识别完成后

```python
# 在 _interview_handler_loop() 中
candidate_text = asr_recognized.sentence
self._emit_candidate_text(candidate_text)  # 触发回调
```

**用途**：记录候选人对话内容到数据库

#### 2. on_bot_sentence(text: str)

**触发点**：机器人文本生成完成后

```python
# 在 stream_interview_llm_chat() 中
if completion_buffer:
    self.history_messages.append(ArkMessage(role="assistant", content=completion_buffer))
    self._emit_bot_text(completion_buffer)  # 触发回调
```

**用途**：记录机器人回复内容到数据库

#### 3. on_bot_audio_chunk(chunk: bytes)

**触发点**：TTS 生成每个音频块时

```python
# 在 handle_tts_response() 中
elif tts_rsp.audio:
    buffer.extend(tts_rsp.audio)
    self._emit_bot_audio_chunk(tts_rsp.audio)  # 触发回调
```

**用途**：累积机器人音频，用于面试结束后持久化

#### 4. on_interview_completed()

**触发点**：面试流程完成时

```python
# 在 _interview_handler_loop() 中
if flow.is_done:
    self._emit_interview_completed()  # 触发回调
```

**用途**：标记面试状态为 `completed`（区别于 `disconnected`）

### 回调异常处理

所有回调调用都包裹在 try-except 中，确保回调失败不影响主流程：

```python
def _emit_bot_text(self, text: str) -> None:
    if not text or not self.on_bot_sentence:
        return
    try:
        self.on_bot_sentence(text)
    except Exception as callback_error:
        self._log(f"[InterviewPersist] on_bot_sentence callback failed: {callback_error}")
```

## 延迟追踪系统

### 时间戳记录

每个对话轮次开始时调用 `_start_turn()` 生成唯一 `turn_id`：

```python
def _start_turn(self) -> None:
    self.current_turn_id = str(uuid.uuid4())
    self.turn_timestamps_ms = {}
```

### 关键事件时间戳

```python
self._log_turn_event("turn_recognized_emitted")      # 识别完成
self._log_turn_event("judge_start")                  # Judge 开始
self._log_turn_event("judge_end")                    # Judge 结束
self._log_turn_event("interviewer_llm_start")        # LLM2 开始
self._log_turn_event("interviewer_llm_first_token")  # LLM2 首 token
self._log_turn_event("interviewer_llm_end")          # LLM2 结束
self._log_turn_event("tts_init_start")               # TTS 初始化开始
self._log_turn_event("tts_init_end")                 # TTS 初始化结束
self._log_turn_event("tts_stream_start")             # TTS 流开始
self._log_turn_event("tts_first_sentence_start")     # TTS 首句开始
self._log_turn_event("tts_done")                     # TTS 完成
```

### 延迟指标计算

```python
def _log_turn_latency_breakdown(self, *, status: str) -> None:
    metrics = {
        "judge_ms": self._duration_ms("judge_start", "judge_end"),
        "llm2_ttft_ms": self._duration_ms("interviewer_llm_start", "interviewer_llm_first_token"),
        "llm2_total_ms": self._duration_ms("interviewer_llm_start", "interviewer_llm_end"),
        "tts_init_ms": self._duration_ms("tts_init_start", "tts_init_end"),
        "rec_to_first_sentence_ms": self._duration_ms("turn_recognized_emitted", "tts_first_sentence_start"),
        "rec_to_tts_done_ms": self._duration_ms("turn_recognized_emitted", "tts_done"),
    }
```

**关键指标**：
- `judge_ms`：Judge 评估耗时
- `llm2_ttft_ms`：LLM2 首 token 延迟（Time To First Token）
- `rec_to_first_sentence_ms`：识别完成到首句播放（用户感知延迟）
- `rec_to_tts_done_ms`：识别完成到回复播放完毕（总延迟）

## 配置项详解

### ASR 配置

```python
asr_app_key: str          # 必填，ASR 应用 Key
asr_access_key: str       # 必填，ASR 访问 Token
asr_resource_id: str      # 可选，ASR 资源 ID（默认从环境变量）
asr_ws_url: str           # 可选，ASR WebSocket 地址
```

**初始化参数**：
- `ASR_INIT_TIMEOUT_SECONDS = 12`：初始化超时
- `ASR_INIT_MAX_ATTEMPTS = 2`：最大重试次数
- `ASR_INIT_FATAL_FAILURE_STREAK = 3`：连续失败阈值

### TTS 配置

```python
tts_app_key: str          # 必填，TTS 应用 Key
tts_access_key: str       # 必填，TTS 访问 Token
tts_speaker: str          # 发音人 ID（默认 zh_female_sajiaonvyou_moon_bigtts）
```

**动态切换**：
```python
# 前端可通过 BotUpdateConfigPayload 动态切换发音人
input_event.payload.speaker  # 新发音人 ID
```

### 面试模式配置

```python
interview_mode: bool                         # 是否启用面试模式
interview_questions: List[Dict[str, Any]]   # 题库（可选，默认 DEFAULT_INTERVIEW_QUESTIONS）
```

**默认题库**（3 题）：
```python
DEFAULT_INTERVIEW_QUESTIONS = [
    {"question_id": "q1", "main_question": "请用1分钟做自我介绍..."},
    {"question_id": "q2", "main_question": "请介绍一个你主导的项目..."},
    {"question_id": "q3", "main_question": "当你遇到复杂问题时，通常如何拆解并推动解决？"},
]
```

## 状态管理

### 服务状态

```python
StateIdle = "Idle"           # 空闲，可接受新输入
StateInProgress = "InProgress"  # 处理中，拒绝新输入
```

**状态转换**：
```
Idle → InProgress （收到候选人回答）
InProgress → Idle （回复播放完成）
```

**并发控制**：
```python
# 在 handle_input_event() 中
if self.state != StateIdle:
    self._log("service is InProgress, will ignore the incoming input")
    continue
```

## 数据流向

### 完整对话流（面试模式）

```
┌──────────────┐
│  WebSocket   │ 用户音频帧
└──────┬───────┘
       ↓
┌──────────────┐
│handle_input  │ 过滤 InProgress、确保 ASR 就绪
└──────┬───────┘
       ↓
┌──────────────┐
│  ASR Client  │ stream_asr() → SaucASRFullServerResponse
└──────┬───────┘
       ↓
┌──────────────┐
│handle_asr    │ 累积文本、检测静音、触发轮次
└──────┬───────┘
       ↓
┌──────────────┐
│   LLM1       │ InterviewJudge.decide() → Decision
│  (Judge)     │
└──────┬───────┘
       ↓
┌──────────────┐
│ InterviewFlow│ receive_candidate_answer() → FlowResponse
└──────┬───────┘
       ↓
┌──────────────┐
│   LLM2       │ stream_interview_llm_chat() → AsyncIterable[str]
│(Interviewer) │
└──────┬───────┘
       ↓
┌──────────────┐
│  TTS Client  │ tts() → EventTTSSentenceStart/End/Done
└──────┬───────┘
       ↓
┌──────────────┐
│  WebSocket   │ 音频输出 + TTSSentenceEnd payload
└──────────────┘
```

## 使用示例

### 基本初始化（面试模式）

```python
from service import VoiceBotService

service = VoiceBotService(
    ark_api_key="your-ark-key",
    llm1_endpoint_id="ep-judge-xxx",        # Judge LLM
    llm2_endpoint_id="ep-interviewer-yyy",  # Interviewer LLM
    llm1_thinking_type="enabled",
    llm2_thinking_type="disabled",
    tts_app_key="your-tts-key",
    tts_access_key="your-tts-token",
    tts_speaker="zh_female_tianmei_moon_bigtts",
    asr_app_key="your-asr-key",
    asr_access_key="your-asr-token",
    interview_mode=True,
    interview_questions=[...],
    on_candidate_sentence=lambda text: print(f"Candidate: {text}"),
    on_bot_sentence=lambda text: print(f"Bot: {text}"),
)

await service.init()
```

### 事件循环集成

```python
async def handler(websocket, path):
    async def async_gen(ws):
        async for message in ws:
            yield convert_binary_to_web_event(message)

    async def fetch_output(ws, output_events):
        async for output_event in output_events:
            await ws.send(convert_web_event_to_binary(output_event))

    outputs = service.handler_loop(async_gen(websocket))
    await fetch_output(websocket, outputs)
```

## 故障排查

### 1. ASR 初始化失败

**症状**：`ASR_INIT_FATAL error=ASRInitUnavailableError`

**排查**：
```bash
# 检查 ASR 配置
env | grep ASR

# 检查网络连通性
curl -I $ASR_WS_URL

# 检查连续失败次数
grep "ASR_INIT_STREAK" logs/backend-*.log
```

**修复**：
- 检查 `asr_access_key` 是否过期
- 增加 `ASR_INIT_MAX_ATTEMPTS`
- 检查 `asr_ws_url` 是否正确

### 2. LLM 响应慢

**症状**：`rec_to_first_sentence_ms` 超过 5 秒

**排查**：
```bash
# 检查 LLM TTFT
grep "interviewer_llm_first_token" logs/interview_logs/<token>/backend.log

# 检查 LLM 并发数
grep "llm_limit" logs/backend-*.log
```

**优化**：
- 增加 `LLM_CONCURRENT_REQUESTS`
- 降低 `reasoning_effort`
- 禁用 `thinking_type`

### 3. 回调函数失败

**症状**：`on_bot_sentence callback failed`

**影响**：不影响语音对话，但数据库可能缺失记录

**排查**：
```python
# 检查回调实现
def on_bot_sentence(text: str):
    try:
        save_to_db(text)
    except Exception as e:
        logging.error(f"Failed to save: {e}")
```

### 4. 服务卡住不响应

**症状**：候选人说话后无反应

**排查**：
```bash
# 检查服务状态
grep "service is InProgress" logs/interview_logs/<token>/backend.log

# 检查 ASR 流状态
grep "ASR_STREAM_RESET" logs/interview_logs/<token>/backend.log
```

**可能原因**：
- 状态未从 `InProgress` 恢复到 `Idle`
- ASR 流异常关闭但未重置
- TTS 未正常完成导致状态未更新

## 性能优化

### LLM 并发控制

```python
# 默认 5 并发
LLM_CONCURRENT_REQUESTS=10

# 在代码中体现为
async with llm_slot():
    await llm.astream()
```

### ASR 轮询优化

```python
ASR_POLL_INTERVAL_SECONDS = 0.2    # 降低可减少静音检测延迟
ASR_SILENCE_LOG_EVERY_TICKS = 10   # 采样日志，避免刷屏
```

### TTS 流式优化

```python
# TTS 流式输出，无需等待完整文本
async for tts_rsp in self.tts_client.tts(source=llm_output):
    # 实时传输音频块
    yield TTSSentenceEndPayload(data=buffer)
```

## 相关测试

```bash
# 测试双 LLM 流程
pytest backend/tests/test_service_dual_llm.py

# 测试事件循环
pytest backend/tests/test_service_event_loop.py

# 测试回调系统
pytest backend/tests/test_service_callbacks.py
```
