# frontend/src/utils/voice_bot_service.ts

## 模块职责
- WebSocket 语音机器人底层客户端：管理连接、收发二进制/JSON 消息、音频播放与回调分发。

## 入口与调用方
- 由 `useVoiceBotService` 实例化并驱动。

## 对外接口（导出项）
- `class VoiceBotService`
- 核心能力：`connect`、`shutdown`、`disconnectWsOnly`、`sendMessage`。

## 关键依赖
- 协议编解码：`frontend/src/utils/index.ts`
- 事件类型：`frontend/src/types.ts`
- Web Audio：用于播放 TTS 音频和计算音量。

## 日志与排障
- 该模块异常通常表现为：
  - 收到消息但 UI 无变化（事件未分发）
  - 有文本无声音（音频解码/播放链路异常）
  - 连接频繁重建（网络或服务端主动断连）

## 常见故障与排查步骤
1. 现象：只收到 JSON 无音频。
- 检查后端是否发送音频帧。
- 检查浏览器自动播放策略与 AudioContext 状态。

2. 现象：音频有播放但字幕不更新。
- 检查 `TTSSentenceStart` 是否到达。
- 检查上层 hook 对事件映射是否完整。

3. 现象：发送消息后服务端无响应。
- 检查打包协议头与事件类型是否匹配。
- 联动后端 `utils.py/event.py` 校验协议一致性。

## 手工验证
- 连接后发送配置更新、触发一轮用户音频、确认收到识别与 TTS 事件并正常播放。
