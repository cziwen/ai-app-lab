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
- `TTSDone`：本轮播报结束，可触发配置更新。
- `BotError`/`Queue*`：展示提示并复位状态。

## 依赖与配置
- WS 地址来自 `useWsUrl`，并自动追加 `token` 查询参数。
- 与录音模块 `useAudioRecorder`、文案状态模块 `useCurrentSentence` 联动。

## 日志与排障
- 所有关键事件都通过 `useLogContent` 记录：`connect`、`receive`、`bot error`。
- 若“有连接无回复”，优先检查是否收到 `BotReady` 和 `SentenceRecognized`。

## 常见故障与排查步骤
1. 现象：连接失败。
- 检查 WS URL、token 参数、后端 `8888` 端口。

2. 现象：识别后没有机器人回答。
- 检查是否收到 `TTSSentenceStart/TTSDone`。
- 检查 `BotError` payload 中 code/message。

3. 现象：排队提示一直不结束。
- 检查后端是否发送 `QueueAdmitted`。
- 联动排查后端 `AdmissionController` 的 active/queue 状态。

## 手工验证
- 连接 -> 说话 -> 收到识别文本 -> 收到流式 bot 文本 -> 播放结束后恢复下一轮录音。
