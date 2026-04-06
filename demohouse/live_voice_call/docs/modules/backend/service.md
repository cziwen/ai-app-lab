# backend/service.py

## 模块概述
`service.py` 是实时语音面试的核心编排层，协调 ASR、Judge LLM（LLM1）、Interviewer LLM（LLM2）、TTS，并通过事件流与 `handler.py` 交互。

## 核心职责
- 驱动面试主循环：ASR -> InterviewFlow/InterviewJudge -> LLM2 -> TTS。
- 管理会话状态（Idle / InProgress）与轮次时间戳。
- 构建 Interviewer 上下文并调用 `INTERVIEWER_SYSTEM_PROMPT`。
- 回调上报候选人文本、机器人文本、机器人音频块。

## 当前行为要点
- 面试提问态包含 `ASK_QUESTION`、`ASK_FOLLOWUP`、`ASK_CLARIFY`。
- 提问态上下文遵循“题干保真、只输出问句、默认不追加题干外句子”的 prompt 约束。
- 当收到 `BotError` 场景或上游连接异常时，状态机与上层 `handler` 联动收尾。

## 关键接口
- `handler_loop(inputs)`：主事件循环入口。
- `_interview_handler_loop(inputs)`：面试模式主流程。
- `stream_interview_llm_chat(context)`：调用 LLM2 生成面试官文本。
- `_build_interview_context(...)`：构建 LLM2 输入上下文。

## 相关测试
- `pytest backend/tests/test_service_interview_llm.py`
- `pytest backend/tests/test_service_asr_init.py`
- `pytest backend/tests/test_service_asr_turn_end.py`
