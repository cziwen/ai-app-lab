# 完整数据流向图

## 1. 准入阶段

```text
候选人打开 /check-in?token=INT-xxx
  -> 前端建立 ws://...:8888?token=INT-xxx&client_id=xxx (client_id 可选，建议传)
  -> handler 校验 token
     - 无效: BotError(INVALID_TOKEN) + close
     - 有效: 进入 InterviewOccupancy.acquire
         - admitted: 进入面试
         - duplicate_token(不同 client_id): BotError(TOKEN_ALREADY_WAITING) + close
         - admitted(相同 client_id 重连接管): 进入面试
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
- finalize 护栏：若旧会话发现 token 已被新 owner 接管（`token_reacquired`），则降级 `disconnected` 并 `skip_complete`。
- `PersistenceQueue` 异步写入存储并触发 `ScoringQueue`。
- persistence 护栏：若写入前 owner 已变化，则记录 `event=interview_persist.complete_skipped reason=token_reacquired`，跳过 `completed` 写入。
- `ScoringQueue` 调用 LLM3 完成场景段评分。
