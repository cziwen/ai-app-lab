# 完整数据流向图

## 1. 准入阶段

```text
候选人打开 /check-in?token=INT-xxx
  -> 前端建立 ws://...:8888?token=INT-xxx
  -> handler 校验 token
     - 无效: BotError(INVALID_TOKEN) + close
     - 有效: 进入 InterviewOccupancy.acquire
         - admitted: 进入面试
         - duplicate_token: BotError(TOKEN_ALREADY_WAITING) + close
         - capacity_full: BotError(INTERVIEW_CAPACITY_FULL) + close
         - other: BotError(SERVICE_UNAVAILABLE) + close
```

## 2. 面试初始化
- `VoiceBotService.init()` 初始化 ASR/TTS。
- `InterviewFlow.produce_interviewer_message()` 输出开场白与首题。
- 后端通过 `TTSSentenceStart/TTSSentenceEnd/TTSDone` 下发机器人语音。

## 3. 对话主循环（双 LLM）

```text
前端 UserAudio
  -> backend/service.handle_input_event
  -> ASR 识别
  -> SentenceRecognized
  -> InterviewFlow.receive_candidate_answer
     -> InterviewJudge.decide (LLM1)
     -> Decision(next_action, next_prompt, reason, coverage_score)
  -> InterviewFlow.produce_interviewer_message
  -> stream_interview_llm_chat (LLM2)
  -> TTS
  -> TTSDone
  -> 下一轮
```

## 4. 关键协议事件
- 下行：`BotReady`、`SentenceRecognized`、`SentencePartialRecognized`、`TTSSentenceStart`、`TTSSentenceEnd`、`TTSDone`、`BotError`
- 上行：`UserAudio`、`BotUpdateConfig`、`ClientHangup`、`ClientEndAnswer`

说明：当前无 `Queue*` 事件链路。

## 5. 结束与持久化
- `ClientHangup` 或流程自然结束后，`handler` 收集 turns 与音频。
- `PersistenceQueue` 异步写入存储并触发 `ScoringQueue`。
- `ScoringQueue` 调用 LLM3 完成场景段评分。
