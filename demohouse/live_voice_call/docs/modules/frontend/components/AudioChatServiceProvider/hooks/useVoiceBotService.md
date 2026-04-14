# frontend/src/components/AudioChatServiceProvider/hooks/useVoiceBotService.ts

## 模块职责
- 封装前端与语音机器人服务的会话控制：连接、断开、消息分发、状态同步。

## 入口与调用方
- 由 `useCallController` 调用。
- 内部创建并持有 `VoiceBotService` 实例。

## 对外接口（导出项）
- `useVoiceBotService`
- 返回：`handleConnect`、`disconnectSession`、`shutdownSession`

## 关键事件处理
- `BotReady`：标记 WS 可用，初始化 bot 消息。
- `SentenceRecognized`：停止录音，写入用户句子。
- `TTSSentenceStart`：流式拼接机器人句子并标记 bot speaking。
- `TTSDone`：本轮播报结束，触发门控判断与 watchdog（默认 1500ms）。
- `BotError`：展示提示并复位状态（如 `TOKEN_ALREADY_WAITING`、`INTERVIEW_CAPACITY_FULL`、`SERVICE_UNAVAILABLE`）。

## 录音门控与防死锁
- 录音启动门控条件：`ttsDoneRef && playbackStoppedRef`。
- `playbackStoppedRef` 在 `onStopPlayAudio` 置位；若 iOS 焦点切换导致播放停在 pause，可能收不到 ended/error。
- 当前实现增加三层兜底：
  1. `visibilitychange/pageshow/focus` 回前台时调用 `service.handleForegroundResume(...)`。
  2. `TTSDone` 后启动 watchdog；若检测 `media-element` 处于 paused 卡住态，主动二次恢复。
  3. 二次恢复仍失败时，强制置位 `playbackStoppedRef` 并执行 `maybeStartRecorder()`，避免无限等待。

导出辅助判定（用于测试）：
- `isStuckMediaPlayback(snapshot)`
- `shouldRunPlaybackWatchdog(wsReady, ttsDone, playbackStopped, botTurnStarted)`

## 依赖与配置
- WS 地址来自 `useWsUrl`，并自动追加 `token` 查询参数。
- 与录音模块 `useAudioRecorder`、文案状态模块 `useCurrentSentence` 联动。

## 日志与排障
- 所有关键事件都通过 `useLogContent` 记录：`connect`、`receive`、`bot error`。
- 若“有连接无回复”，优先检查是否收到 `BotReady` 和 `SentenceRecognized`。
- iOS 恢复链路重点看：
  - `gate: playback watchdog fired ...`
  - `gate: playback watchdog detected stuck media, trying recovery`
  - `gate: playback watchdog forced playback stopped`

## 常见故障与排查步骤
1. 现象：连接失败。
- 检查 WS URL、token 参数、后端 `8888` 端口。

2. 现象：识别后没有机器人回答。
- 检查是否收到 `TTSSentenceStart/TTSDone`。
- 检查 `BotError` payload 中 code/message。

3. 现象：进入面试前即被拒绝或提示服务繁忙。
- 检查 `BotError` 的 `code/message`（如 `TOKEN_ALREADY_WAITING`、`INTERVIEW_CAPACITY_FULL`）。
- 联动排查后端 `InterviewOccupancy` 配置与 Redis 连接状态（`MAX_ACTIVE_INTERVIEWS`、`INTERVIEW_OCCUPANCY_*`）。

## 手工验证
- 基础链路：连接 -> 说话 -> 收到识别文本 -> 收到流式 bot 文本 -> 播放结束后恢复下一轮录音。
- iOS 回归：通话中切到外部音频 App/网页 -> 切回页面 -> 连续 3 轮问答，确认不会卡在 bot 回合末尾。
