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
- `VoiceBotService.init()` 初始化 ASR/TTS（实时链路）。
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
- finalize 会在评分前对 candidate canonical 音频执行 STT 二次识别（可选）：
  - 成功：按候选人轮次长度配额复写 candidate turns。
  - 部分成功：未覆盖轮次保留 ASR 文本（partial fallback）。
  - 失败/超时/配置无效：整场回退 ASR（full fallback）。
- `PersistenceQueue` 异步写入存储并触发 `ScoringQueue`。
- persistence 护栏：若写入前 owner 已变化，则记录 `event=interview_persist.complete_skipped reason=token_reacquired`，跳过 `completed` 写入。
- `ScoringQueue` 调用 LLM3 完成场景段评分（score_inputs 基于复写后的 canonical turns）。

## 6. 重试与恢复
- 前端重连：
  - 退避间隔：`500ms -> 1000ms -> 2000ms -> 3000ms`（封顶 3000ms）。
  - 总预算：`FRONTEND_RECONNECT_MAX_SECONDS`（默认 15 秒）。
  - 预算耗尽后置 `reconnectExhausted=true`，上层页面触发自动结束流程。
- 后端断连宽限：
  - 网络断开先标记 `disconnected`（宽限 `INTERVIEW_RECONNECT_GRACE_SECONDS`，默认 30 秒），而不是立即 completed。
  - 同 token + 相同 `client_id` 重连可接管 owner，并继续会话。
- checkpoint 恢复：
  - handler 会加载最近 runtime checkpoint 传给 `VoiceBotService`。
  - service 会根据恢复状态选择 `done / wrap_up / question_start` 恢复模式。
- 持久化重试：
  - `PersistenceQueue` 失败时按指数退避重试（默认最多 3 次，基线 0.3 秒）。
