# frontend/src/call-ui/hooks/useCallController.ts

## 模块概述
`useCallController` 是通话页控制中枢，负责聚合实时会话状态、控制栏操作、自动连接、自动结束与页面跳转。

## 主要职责
- 统一输出 UI 状态（连接态、说话态、字幕、倒计时、重连提示）。
- 处理控制操作（connect / hangUp / toggleMic / endAnswer 等）。
- 在 real/mock 模式间切换并清理资源。
- 协调 `useVoiceBotService`（连接、重连、挂断消息）与录音/媒体流生命周期。

## 自动连接（real 模式）
- 当满足以下条件时自动调用 `handleConnect()`：
  - `mode === 'real'`
  - `!wsConnected`
  - `!endingRef.current`
  - `!connectingRef.current`
  - `!isRecovering`
  - `!reconnectExhausted`
- 使用 1.2 秒 guard 防止重复触发连接。

## 自动结束流程（当前实现）
触发条件有两类：
1. 已连接后断开，且当前不在重连中（`wasConnected && !wsConnected && !isRecovering`）。
2. 重连预算耗尽（`reconnectExhausted === true`）。

流程：
1. `startAutoFinishFlow()`：停止采集、断开会话。
2. 若仍有 bot 播放中：进入 `waiting_last_audio`。
3. 播放结束后进入 `countdown`（默认 15 秒）。
4. 倒计时归零跳转 `/hangup-result`。

## 主动挂断流程
- `hangUp` 会调用 `finishImmediately()`：
  - 先 `notifyClientHangup()`（发送 `ClientHangup`，带 drain）
  - 再 `shutdownSession()`
  - 最后立即跳转结果页
- 不走自动结束倒计时。

## 与 `useVoiceBotService` 的关键联动
- 输入：`handleConnect`、`disconnectSession`、`shutdownSession`、`notifyClientHangup`、`notifyClientEndAnswer`、`isRecovering`、`reconnectExhausted`。
- 输出 UI：
  - `reconnectNotice`: `isRecovering` 时显示“网络波动，正在重连…”
  - `endNotice`: 倒计时阶段显示“面试已结束，N 秒后自动跳转结果页”

## 对外接口
- `useCallController(): CallController`
- `formatDuration(seconds): string`
