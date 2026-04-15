# backend/service.py

## 模块概述
`service.py` 是实时面试编排核心，负责把输入音频、状态机决策、LLM 文本生成和 TTS 播放串成单轮闭环，并通过回调把 turn 文本/音频/checkpoint 交给 `handler.py` 落盘。

## 面试主链路（当前实现）
1. 初始化：`VoiceBotService.init()` 构建 `InterviewFlow` + `InterviewJudge`，并按题目构建场景分段上下文。
2. 首轮播报：
- 新会话：先播开场，再播首题（两段 scripted TTS，不走 LLM2）。
- 恢复会话：根据 checkpoint 进入 `RESUME_MODE_DONE | WRAP_UP | QUESTION_START`。
3. 候选人回答：
- 通过 ASR 得到 `SentenceRecognized`。
- 进入 `InterviewFlow.receive_candidate_answer()`，内部调用 LLM1（Judge）产出 `Decision`。
4. 面试官回复：
- `InterviewFlow.produce_interviewer_message()` 先给出“下一步原始题干/追问/澄清/结束语”。
- `_build_interview_context(...)` 结合 `Decision`、场景切换信息生成 LLM2 上下文。
- `stream_interview_llm_chat(...)` 调 LLM2，`handle_tts_response(...)` 回传语音事件。
5. 回合结束：状态回到 `Idle`，等待下一次候选人发言。

## 回答回合超时与挂断
- 每次进入 `WAIT_ANSWER` 都会启动“无语音首句超时”计时。
- 若在 `ASR_NO_SPEECH_HANGUP_TIMEOUT_MS`（默认 30000ms）内 `asr_buffer` 仍为空，会触发服务端挂断请求回调 `on_client_hangup_requested`。
- 该路径会复用现有 hangup 收尾链路（不是新增 completed reason）。
- `ASR_MANUAL_TURN_END_ENABLED=true` 只会禁用“有文本后的 silence_timeout 自动收口”，不会禁用“完全无输入超时挂断”。

## 恢复与重试机制

### 断线恢复（checkpoint）
- 支持 runtime checkpoint 恢复。
- 若 checkpoint 状态是：
  - `DONE`：直接触发 completed 回调，不再发音频。
  - `WRAP_UP`：补播结束语后 completed。
  - 其他中间态：回退到当前题起点（`rewind_to_question_start`），重新播该题。
- 恢复后首个候选人回答会启用“低信号保护”：若仅是“嗯/啊/then/um”等低信息输入，会先播提醒语，不进入 Judge。

### ASR 初始化重试
- `_ensure_asr_ready()` 默认重试参数：
  - `ASR_INIT_MAX_ATTEMPTS=2`
  - 单次超时 `ASR_INIT_TIMEOUT_SECONDS=12`
  - 退避 `ASR_INIT_RETRY_BACKOFF_SECONDS=0.2`
- 连续失败达到 `ASR_INIT_FATAL_FAILURE_STREAK=3` 时，向前端发送 `BotError(code=ASR_INIT_UNAVAILABLE)`。

### 生成降级
- 若 LLM2/TTS 在某轮失败，当前轮会降级为 scripted 文本（优先用状态机给出的 `next_interviewer_text`），避免会话直接中断。

## 关键接口
- `handler_loop(inputs)`：总入口（面试模式/普通模式分流）。
- `_interview_handler_loop(inputs)`：面试模式主循环。
- `stream_interview_llm_chat(context)`：LLM2 流式生成。
- `_build_interview_context(...)`：构建 LLM2 指令上下文。
- `_emit_interview_runtime_checkpoint()`：回调输出 runtime checkpoint。

## 相关测试
- `pytest backend/tests/test_service_interview_llm.py`
- `pytest backend/tests/test_service_asr_init.py`
- `pytest backend/tests/test_service_asr_turn_end.py`
- `pytest backend/tests/test_service_resume_guard.py`
