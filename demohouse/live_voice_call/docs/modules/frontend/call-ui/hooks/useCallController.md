# frontend/src/call-ui/hooks/useCallController.ts

## 模块职责
- 通话页控制器：统一管理 UI 状态、控制栏动作、mock/real 模式切换、挂断收尾。

## 入口与调用方
- 由 `CallInterviewPage` 使用，向页面组件提供 `CallController`。

## 对外接口（导出项）
- `useCallController`
- `formatDuration`

## 关键状态与流程
- 模式：`real`（真实后端）/ `mock`（本地脚本）。
- 连接守护：`real` 模式下自动尝试连接，失败/断连触发结束流程。
- 收尾阶段：`idle -> waiting_last_audio -> countdown`，倒计时后跳转结果页。
- 资源释放：统一停止录音、关闭媒体流、复位音量与摄像头/共享状态。

## 依赖与配置
- 依赖 `useVoiceBotService`、`useAudioRecorder`、`useAudioChatState`、`useSessionAuth`。
- 本地调试入口受 `token=DEBUG + localhost` 限制。

## 日志与排障
- mock 模式日志保存在本地状态，real 模式依赖 `useLogContent`。
- “自动挂断”相关问题优先检查：`wsConnected` 边沿变化与 `endPhase` 状态迁移。

## 常见故障与排查步骤
1. 现象：连接后立即跳转结果页。
- 检查 `wsConnected` 是否短暂为 true 后又变 false。
- 检查后端是否主动断开或网络抖动。

2. 现象：挂断后摄像头/共享未释放。
- 检查 `cleanupCaptureResources` 是否执行。
- 检查 `mediaStreamsRef` 中 track 是否 stop。

3. 现象：debug 面板不可见。
- 仅 `localhost` 且 URL 含 `token=DEBUG` 时显示。

## 手工验证
- real 模式下验证连接、说话、挂断与自动跳转；mock 模式下验证脚本字幕与消息面板更新。
