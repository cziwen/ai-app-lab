# frontend/src/components/AudioChatServiceProvider/hooks/useVoiceBotService.ts

## 模块职责
- 管理实时语音会话生命周期：连接、断开、重连、收发事件、音频门控。
- 维护“播报完成后再开录音”的门控，避免 TTS 与录音互相抢占。
- 处理 iOS 回前台音频恢复与 watchdog 降级。

## 对外接口（实际返回）
- `handleConnect`
- `disconnectSession`
- `shutdownSession`
- `notifyClientHangup`
- `notifyClientEndAnswer`
- `isRecovering`
- `reconnectExhausted`

## 建连与鉴权参数
- 使用 `appendTokenToWsUrl(...)` 自动拼接：
  - `token`
  - `client_id`（页面级固定 ID，用于后端重连接管）
- `client_id` 会在整个页面生命周期内复用，不会每次重连生成新值。

## 自动重连（当前实现）
- 开启条件：非手动断开，且 `autoReconnectEnabled=true`。
- 重连退避：`RECONNECT_DELAYS_MS = [500, 1000, 2000, 3000]` ms。
- 总时限：`FRONTEND_RECONNECT_MAX_SECONDS`（默认 `15` 秒，可由 `MODERN_PUBLIC_FRONTEND_RECONNECT_MAX_SECONDS` 或 `FRONTEND_RECONNECT_MAX_SECONDS` 配置）。
- 超时后：
  - 停止重连
  - `reconnectExhausted=true`
  - 由上层 `useCallController` 触发自动结束流程。

## 关闭事件处理
- `onClose` 默认会尝试自动重连。
- 若关闭帧为面试正常结束（`code=4001` 且 reason 为空或 `interview_completed`），则不重连。
- 手动断开/挂断会显式关闭重连并清理状态。

## 关键事件处理
- `BotReady`：标记 WS ready，初始化 bot 消息占位。
- `SentencePartialRecognized`：更新候选人实时字幕。
- `SentenceRecognized`：停止录音，写入候选人文本与消息列表。
- `TTSSentenceStart`：进入 bot 说话态，拼接 bot 文本。
- `TTSDone`：标记播报完成，触发录音门控和 playback watchdog。
- `BotError`：停止重连，提示错误并重置会话状态。

## 录音门控与 iOS 恢复
- 门控条件：`ttsDoneRef && playbackStoppedRef`。
- `TTSDone` 后会启动 watchdog（默认 1500ms）；若检测到 `media-element` 假播放卡死（`playing=true` 但 `audio.paused=true && !ended`）：
  - 先尝试 `handleForegroundResume('tts_watchdog')`
  - 若仍卡住，强制放行下一轮录音（置 `playbackStoppedRef=true`）。
- 生命周期恢复触发：`visibilitychange`、`pageshow`、`focus`。

## 相关测试
- `frontend/src/components/AudioChatServiceProvider/hooks/useVoiceBotService.reconnect-guard.test.ts`
- `frontend/src/components/AudioChatServiceProvider/hooks/useAudioRecorder.test.ts`
