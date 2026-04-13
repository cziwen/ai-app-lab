# frontend/src/utils/voice_bot_service.ts

## 模块职责
WebSocket 语音机器人底层客户端，负责：
- 建立和管理 WebSocket 连接
- 收发二进制/JSON 混合协议消息
- 音频解码、播放与音量分析
- 路由策略自适应（media-element / web-audio-fallback）
- 事件回调分发与生命周期管理

## 入口与调用方
- 由 `hooks/use-voice-bot-service.ts` 实例化并驱动
- 通过构造函数传入回调处理器，完成事件分发

## 对外接口（导出项）

### 类定义
```typescript
class VoiceBotService {
  constructor(props: IVoiceBotService)

  // 连接管理
  connect(overrideWsUrl?: string): Promise<WebSocket>
  disconnectWsOnly(): void
  shutdown(): void

  // 音频控制
  unlockAudio(): Promise<void>
  handleForegroundResume(trigger: string): Promise<void>
  getPlaybackSnapshot(): PlaybackSnapshot
  stopAllMedia(): void

  // 消息发送
  sendMessage(message: WebRequest): void
}
```

### 回调接口
```typescript
interface IVoiceBotService {
  ws_url: string
  handleJSONMessage: (json: JSONResponse) => void
  onStartPlayAudio: (data: ArrayBuffer) => void
  onStopPlayAudio: () => void
  onAudioLevelChange?: (level: number) => void
  onAudioUnlockedChange?: (unlocked: boolean) => void
  onAudioRouteModeChange?: (mode: AudioRouteMode) => void
  onLog?: (message: string) => void
  onClose?: (event: CloseEvent) => void
  onError?: (event: Event) => void
}
```

### 音频路由模式
```typescript
type AudioRouteMode = 'media-element' | 'web-audio-fallback'

type PlaybackSnapshot = {
  route: AudioRouteMode
  playing: boolean
  queueLength: number
  audioPaused: boolean | null
  audioEnded: boolean | null
  audioReadyState: number | null
  audioCtxState: AudioContextState
}
```

## WebSocket 二进制协议实现

### 协议结构
完整消息 = Header（8 字节）+ Payload（可变长度）

#### Header 字段定义（8 字节）
```
Byte 0: [4 bits 协议版本 | 4 bits Header 大小]
Byte 1: [4 bits 消息类型 | 4 bits 序列标志]
Byte 2: [4 bits 序列化方式 | 4 bits 压缩方式]
Byte 3: [8 bits 保留位]
Byte 4-7: [32 bits Payload 长度（大端序）]
```

#### 消息类型常量
```typescript
CLIENT_FULL_REQUEST: 0b0001          // 客户端完整请求（JSON）
CLIENT_AUDIO_ONLY_REQUEST: 0b0010    // 客户端纯音频
SERVER_FULL_RESPONSE: 0b1001         // 服务端完整响应（JSON）
SERVER_AUDIO_ONLY_RESPONSE: 0b1011   // 服务端纯音频（MP3）
```

### 编码实现（pack）
```typescript
function pack(req: WebRequest): Blob {
  if (req.payload) {
    // 完整请求：Header + JSON 字符串
    const header = generateWSHeader(CLIENT_FULL_REQUEST)
    const json = JSON.stringify(req)
    const encoded = new TextEncoder().encode(json)
    header.setUint32(4, encoded.length, false)  // 设置 Payload 长度
    return new Blob([header, json])
  }

  // 纯音频请求：Header + 音频二进制
  const header = generateWSHeader(CLIENT_AUDIO_ONLY_REQUEST)
  const data = req.data || new Blob()
  header.setUint32(4, data.size, false)
  return new Blob([header, data])
}

function generateWSHeader(msgType: number): DataView {
  const buffer = new ArrayBuffer(8)
  const dataView = new DataView(buffer)

  // Byte 0: 版本(0b0001) + Header 大小(0b0001)
  dataView.setUint8(0, (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE)

  // Byte 1: 消息类型 + 无序列号标志
  dataView.setUint8(1, (msgType << 4) | NO_SEQUENCE)

  // Byte 2: JSON 序列化 + 无压缩
  dataView.setUint8(2, (JSON << 4) | NO_COMPRESSION)

  // Byte 3: 保留位
  dataView.setUint8(3, 0x00)

  return dataView
}
```

### 解码实现（decodeWebSocketResponse）
```typescript
function decodeWebSocketResponse(resp: ArrayBuffer): IWebSocketResponse {
  const view = new DataView(resp)

  // 解析 Header
  const header_size = view.getUint8(0) & 0x0f        // 低 4 位
  const messageType = (view.getUint8(1) >> 4) & 0x0f // 高 4 位

  // 提取 Payload（跳过 Header）
  const payload = resp.slice(header_size * 4)  // Header 大小 * 4 字节
  const payloadSize = new DataView(payload).getUint32(0)
  const payloadBody = payload.slice(4)  // 跳过长度字段

  if (messageType === SERVER_AUDIO_ONLY_RESPONSE) {
    // 纯音频：直接返回 ArrayBuffer
    return {
      messageType: SERVER_AUDIO_ONLY_RESPONSE,
      payload: payloadBody.slice(0, payloadSize)
    }
  }

  // 完整响应：解析 JSON
  return {
    messageType: SERVER_FULL_RESPONSE,
    payload: JSON.parse(new TextDecoder().decode(payloadBody))
  }
}
```

## 音频路由双策略

### 路由模式选择
```typescript
// 构造函数中自动检测
this.audioRouteMode = isLikelySafariMobile()
  ? 'media-element'        // iOS Safari 默认
  : 'web-audio-fallback'   // 其他浏览器

// 运行时动态切换（降级）
setAudioRouteMode(mode: AudioRouteMode) {
  if (this.audioRouteMode === mode) return
  this.audioRouteMode = mode
  this.onAudioRouteModeChange?.(mode)
  this.log(`audio route switched to ${mode}`)
}
```

### 策略 1：media-element（移动端优先）
```typescript
async playChunkViaMediaElement(data: ArrayBuffer) {
  // 创建 Audio 元素
  if (!this.mediaAudio) {
    this.mediaAudio = new Audio()
    this.mediaAudio.preload = 'auto'
    this.mediaAudio.playsInline = true  // 内联播放，防止全屏
  }

  // 创建 Object URL
  this.mediaObjectUrl = URL.createObjectURL(
    new Blob([data], { type: 'audio/mpeg' })
  )
  this.mediaAudio.src = this.mediaObjectUrl

  // 事件监听
  this.mediaAudio.onended = () => {
    URL.revokeObjectURL(this.mediaObjectUrl)
    this.mediaObjectUrl = null
    this.playNextAudioChunk()
  }

  this.mediaAudio.onerror = () => {
    this.log('media-element playback failed, fallback to web-audio')
    this.setAudioRouteMode('web-audio-fallback')  // 降级
    this.audioChunks.unshift(data)  // 重新入队
    this.playNextAudioChunk()
  }

  try {
    await this.mediaAudio.play()
    this.onAudioLevelChange?.(0.3)  // 固定音量指示
  } catch (error) {
    // play() 被浏览器拒绝（自动播放策略）
    this.setAudioRouteMode('web-audio-fallback')
    this.audioChunks.unshift(data)
    this.playNextAudioChunk()
  }

  // iOS 焦点切换时可能只触发 pause，不触发 ended/error
  this.mediaAudio.onpause = () => {
    this.tryResumeMediaPlayback('media_onpause', true)
  }
}
```

#### 优势与局限
- **优势**：iOS Safari 兼容性好，内联播放稳定
- **局限**：无法实时分析音频波形，仅提供固定音量值

### 前台恢复与降级策略（iOS）
```typescript
async handleForegroundResume(trigger: string) {
  await this.unlockAudio()
  const snapshot = this.getPlaybackSnapshot()
  this.logPlaybackSnapshot(`audio foreground resume trigger=${trigger}`, snapshot)
  if (snapshot.route !== 'media-element') return
  await this.tryResumeMediaPlayback(`foreground_${trigger}`, true)
}
```

恢复判定与兜底：
- 仅在 `media-element + audio.paused + !audio.ended + 页面可见` 时尝试恢复。
- 恢复成功：继续当前播报与队列消费。
- 恢复失败：执行降级 `clearPlaybackBuffer()`，触发 `onStopPlayAudio()`，避免门控无限等待。
- 所有恢复路径会打印 snapshot（`route/playing/queue/paused/ended/ready/ctx`）便于定位。

### 策略 2：web-audio-fallback（桌面端默认）
```typescript
async playChunkViaWebAudio(data: ArrayBuffer) {
  try {
    // 确保 AudioContext 运行中
    if (this.audioCtx.state !== 'running') {
      await this.audioCtx.resume()
    }

    // 解码 MP3 音频
    const audioBuffer = await this.audioCtx.decodeAudioData(
      new Uint8Array(data).buffer
    )

    // 构建音频图
    const source = this.audioCtx.createBufferSource()
    const analyser = this.audioCtx.createAnalyser()
    analyser.fftSize = 1024

    source.buffer = audioBuffer
    source.connect(analyser)
    analyser.connect(this.audioCtx.destination)

    // 监听播放结束
    source.addEventListener('ended', () => this.playNextAudioChunk())

    // 启动实时分析
    this.analyser = analyser
    this.analyserData = new Uint8Array(analyser.fftSize)
    this.startAnalyserLoop()

    source.start(0)
  } catch (error) {
    this.log(`web-audio decode/play failed error=${String(error)}`)
    this.playNextAudioChunk()  // 跳过该帧
  }
}
```

#### 优势与局限
- **优势**：实时波形分析、精确音量计算、低延迟
- **局限**：iOS Safari 可能受自动播放策略限制

## Web Audio API 使用细节

### AudioContext 生命周期管理
```typescript
// 初始化（兼容 Safari）
const getAudioContext = () => {
  const audioContextCtor =
    window.AudioContext ||
    (window as any).webkitAudioContext as typeof AudioContext
  if (!audioContextCtor) {
    throw new Error('AudioContext unavailable')
  }
  return new audioContextCtor()
}

// 自动恢复
async unlockAudio() {
  if (this.audioCtx.state === 'closed') {
    this.audioCtx = getAudioContext()  // 重新创建
  }
  if (this.audioCtx.state !== 'running') {
    await this.audioCtx.resume()  // 唤醒挂起的上下文
  }
  const unlocked = this.audioCtx.state === 'running'
  this.onAudioUnlockedChange?.(unlocked)
}

// 清理
stopAllMedia() {
  // 停止音频源
  if (this.source) {
    this.source.stop()
    this.source.disconnect()
    this.source = undefined
  }

  // 断开分析器
  if (this.analyser) {
    this.analyser.disconnect()
    this.analyser = undefined
  }

  // 关闭上下文
  if (this.audioCtx.state !== 'closed') {
    this.audioCtx.close()
  }
}
```

### 音频分析与音量计算
```typescript
// 启动实时分析循环
startAnalyserLoop() {
  if (!this.analyser || !this.analyserData) return

  const tick = () => {
    if (!this.analyser || !this.analyserData) {
      this.analyserFrameId = null
      return
    }

    // 获取时域数据（波形）
    this.analyser.getByteTimeDomainData(this.analyserData)

    // 计算 RMS（均方根）音量
    let sum = 0
    for (let i = 0; i < this.analyserData.length; i++) {
      const v = (this.analyserData[i] - 128) / 128  // 归一化到 [-1, 1]
      sum += v * v
    }
    const rms = Math.sqrt(sum / this.analyserData.length)

    // 归一化到 [0, 1]（动态范围：0.01 ~ 0.13）
    const normalizedLevel = Math.max(0, Math.min(1, (rms - 0.01) / 0.12))
    this.onAudioLevelChange?.(normalizedLevel)

    // 递归调度
    this.analyserFrameId = window.requestAnimationFrame(tick)
  }

  this.analyserFrameId = window.requestAnimationFrame(tick)
}

// 停止分析循环
stopAnalyserLoop() {
  if (this.analyserFrameId !== null) {
    window.cancelAnimationFrame(this.analyserFrameId)
    this.analyserFrameId = null
  }
}
```

### 音频队列管理
```typescript
// 队列式播放
private audioChunks: ArrayBuffer[] = []
private playing = false

handleAudioOnlyResponse(data: ArrayBuffer) {
  this.audioChunks.push(data)  // 入队

  if (!this.playing) {
    this.onStartPlayAudio(data)
    this.playing = true
    this.playNextAudioChunk()  // 启动播放
  }
}

async playNextAudioChunk() {
  if (this.disposed) {
    this.playing = false
    return
  }

  const data = this.audioChunks.shift()  // 出队
  if (!data) {
    this.onStopPlayAudio()
    this.playing = false
    return
  }

  // 根据路由模式选择播放策略
  if (this.audioRouteMode === 'media-element') {
    await this.playChunkViaMediaElement(data)
  } else {
    await this.playChunkViaWebAudio(data)
  }
}
```

## 关键依赖

### 协议编解码
- `frontend/src/utils/index.ts`：`pack`、`decodeWebSocketResponse`、`generateWSHeader`
- `frontend/src/constant.ts`：协议常量定义

### 类型定义
- `frontend/src/types.ts`：`WebRequest`、`JSONResponse`、`IWebSocketResponse`、`EventType`

### 浏览器 API
- `WebSocket`：双向通信
- `AudioContext`：音频处理引擎
- `AnalyserNode`：频谱/波形分析
- `AudioBufferSourceNode`：音频播放源
- `HTMLAudioElement`：媒体元素播放

## 日志与排障

### 日志输出格式
```typescript
private log(message: string) {
  this.onLog?.(`[AudioRuntime] ${message}`)
}

// 关键日志点
- audio init route=${mode} audio_ctx_state=${state}
- ws connected audio_ctx_state=${state} route=${mode}
- audio unlock attempted unlocked=${unlocked} audio_ctx_state=${state}
- audio chunk played with ${route} route
- audio route switched to ${mode}
```

### 常见故障与排查步骤

#### 1. 只收到 JSON 无音频
**现象**：收到 `TTSSentenceStart` 事件但无声音

**排查**：
- 检查后端是否发送 `SERVER_AUDIO_ONLY_RESPONSE` 消息
- 检查浏览器控制台是否有 `decodeAudioData` 错误
- 检查 AudioContext 状态：`audio_ctx_state=suspended` 表示未解锁
- 手动调用 `unlockAudio()` 并观察用户交互是否触发

**解决**：
```typescript
// 确保用户交互后立即解锁
window.addEventListener('click', () => {
  voiceBotService.unlockAudio()
}, { once: true })
```

#### 2. 音频有播放但字幕不更新
**现象**：听到声音但 UI 无文本显示

**排查**：
- 检查 `TTSSentenceStart` 事件是否到达 `handleJSONMessage`
- 检查上层 hook 对 `EventType.TTSSentenceStart` 的映射是否完整
- 检查事件 payload 结构是否符合预期

**解决**：
```typescript
// 确保完整处理事件
handleJSONMessage: (json: JSONResponse) => {
  if (json.event === EventType.TTSSentenceStart) {
    const text = json.payload?.text
    console.log('TTS text:', text)  // 验证
  }
}
```

#### 3. 发送消息后服务端无响应
**现象**：调用 `sendMessage` 但后端无反应

**排查**：
- 检查 WebSocket 状态：`ws.readyState !== WebSocket.OPEN`
- 检查协议头：消息类型、序列化方式是否匹配后端预期
- 联动后端 `utils.py/event.py` 校验协议一致性
- 检查 payload 序列化：`JSON.stringify(req)` 是否成功

**解决**：
```typescript
// 确保连接就绪
await voiceBotService.connect(wsUrl)
voiceBotService.sendMessage({
  event: 'BotUpdateConfig',
  payload: { key: 'value' }
})
```

#### 4. 音频路由频繁切换
**现象**：日志显示 `audio route switched to web-audio-fallback`

**原因**：`media-element` 播放失败（权限/编解码问题）

**解决**：
- iOS Safari：检查是否允许自动播放
- 桌面端：优先使用 `web-audio-fallback`
- 监听 `onAudioRouteModeChange` 并记录降级原因

#### 5. 播放延迟或卡顿
**现象**：音频断断续续

**排查**：
- 检查队列长度：`audioChunks.length` 是否堆积
- 检查网络：WebSocket 是否丢包
- 检查解码性能：`decodeAudioData` 耗时

**解决**：
```typescript
// 监控队列深度
if (this.audioChunks.length > 5) {
  this.log(`audio queue backlog: ${this.audioChunks.length}`)
}
```

## 手工验证

### 完整测试流程
1. **连接建立**
```typescript
const service = new VoiceBotService({
  ws_url: 'wss://example.com/ws',
  handleJSONMessage: (json) => console.log('JSON:', json),
  onStartPlayAudio: () => console.log('播放开始'),
  onStopPlayAudio: () => console.log('播放结束'),
  onAudioLevelChange: (level) => console.log('音量:', level),
})
await service.connect()
```

2. **音频解锁**
```typescript
await service.unlockAudio()
// 观察日志：audio unlock attempted unlocked=true
```

3. **发送配置**
```typescript
service.sendMessage({
  event: 'BotUpdateConfig',
  payload: { language: 'zh-CN' }
})
```

4. **发送音频**
```typescript
const audioBlob = new Blob([audioData], { type: 'audio/pcm' })
service.sendMessage({
  event: 'UserAudio',
  data: audioBlob
})
```

5. **接收验证**
- 收到 `SentenceRecognized`：识别成功
- 收到 `TTSSentenceStart`：TTS 开始
- 收到音频帧：播放流畅
- 音量指示器波动：分析正常

6. **清理**
```typescript
service.shutdown()
// 验证：所有媒体资源释放、WebSocket 关闭
```

## 性能优化建议

### 内存管理
- 及时释放 Object URL：`URL.revokeObjectURL()`
- 限制队列长度：超过阈值时跳帧
- 复用 AudioContext：避免频繁创建/销毁

### 延迟优化
- 预热 AudioContext：首次交互时立即 `resume()`
- 降低 FFT 大小：`analyser.fftSize = 512`（降低 CPU 占用）
- 使用 Worker 解码：大文件时移至后台线程

### 兼容性
- iOS Safari：优先使用 `media-element`，禁用全屏播放
- Chrome/Edge：优先使用 `web-audio-fallback`，启用音量分析
- Firefox：检查 `setSinkId` 支持，降级处理

## 相关文档
- [WebSocket 协议规范](../../backend/utils/protocol.md)
- [音频编解码器](../../backend/services/tts.md)
- [前端音频录制](./use-recorder.md)
