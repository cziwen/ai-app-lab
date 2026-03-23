# frontend/src/routes/check-in.tsx

## 模块职责
候选人面试入口页面，负责面试前的完整设备检查流程：
- 加载面试配置的必检项（扬声器、麦克风、摄像头、屏幕共享）
- 串行引导候选人完成设备测试
- 验证设备可用性并持久化媒体流
- 全部通过后才允许进入正式面试页

## 入口与调用方
- 路由路径：`/check-in?token={interview_token}`
- 无需身份验证，通过 token 参数访问
- 使用 `useSessionAuth` 管理权限状态与媒体流引用

## 对外接口（导出项）

### 组件定义
```typescript
export const CheckInPage: React.FC
```

## 核心功能

### 1. 面试配置加载

#### 读取必检项配置
```typescript
useEffect(() => {
  let active = true
  const loadCheckinConfig = async () => {
    if (!token) {
      if (active) {
        setRequiredSteps([...DEFAULT_REQUIRED_STEPS])  // ['speaker', 'mic']
        setLoadingCheckinConfig(false)
      }
      return
    }

    setLoadingCheckinConfig(true)
    try {
      const response = await fetch(
        `${API_URL}/api/public/interviews/${encodeURIComponent(token)}/access`
      )
      if (!response.ok) {
        throw new Error('面试链接无效或已失效')
      }
      const data = await response.json()
      if (!active) return

      // 从后端配置解析必检项
      const nextRequired = normalizeRequiredCheckins(
        data?.interview?.required_checkins
      )
      setRequiredSteps(nextRequired)
    } catch (_error) {
      if (!active) return
      setRequiredSteps([...DEFAULT_REQUIRED_STEPS])
      setErrorMessage('读取面试检查项失败，已按默认检查项（扬声器/麦克风）处理。')
    } finally {
      if (active) {
        setLoadingCheckinConfig(false)
      }
    }
  }
  loadCheckinConfig()
  return () => {
    active = false
  }
}, [token])
```

#### 必检项类型定义
```typescript
type CheckInKey = 'speaker' | 'mic' | 'camera' | 'screen'
type PermissionStatus = 'pending' | 'granted' | 'denied'

const DEFAULT_REQUIRED_STEPS: CheckStep[] = ['speaker', 'mic']

const STEP_LABEL: Record<CheckStep, string> = {
  speaker: '扬声器',
  mic: '麦克风',
  camera: '摄像头',
  screen: '屏幕共享'
}
```

#### 必检项规范化
```typescript
const normalizeRequiredCheckins = (input: unknown): CheckStep[] => {
  if (!Array.isArray(input)) {
    return [...DEFAULT_REQUIRED_STEPS]
  }

  const seen = new Set<CheckStep>()
  for (const item of input) {
    if (typeof item !== 'string') continue
    const normalized = item.trim() as CheckStep
    if (ORDERED_STEPS.includes(normalized)) {
      seen.add(normalized)
    }
  }

  // 保持固定顺序：speaker -> mic -> camera -> screen
  return ORDERED_STEPS.filter(step => seen.has(step))
}
```

### 2. 权限初始化引导

#### Bootstrap 流程
```typescript
const bootstrapPermissions = useCallback(async () => {
  setPermissionBootstrapState('requesting')
  setPermissionBootstrapError('')
  logCheckin(`permission bootstrap start required_steps=${requiredSteps.join(',')}`)
  cleanupBootstrapMedia()

  if (!navigator.mediaDevices?.getUserMedia) {
    setPermissionBootstrapState('failed')
    setPermissionBootstrapError('当前浏览器不支持权限初始化，请更换浏览器。')
    logCheckin('permission bootstrap failed unsupported getUserMedia')
    return
  }

  const needAudio = requiredSteps.includes('mic')
  const needVideo = requiredSteps.includes('camera')

  if (!needAudio && !needVideo) {
    setPermissionBootstrapState('granted')
    await loadDevices()
    logCheckin('permission bootstrap skip media request (no mic/camera required)')
    return
  }

  try {
    // 一次性请求所有需要的权限
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: needAudio,
      video: needVideo
    })
    bootstrapMediaStreamRef.current = stream
    await loadDevices()
    setPermissionBootstrapState('granted')
    logCheckin(
      `permission bootstrap granted audio=${needAudio ? '1' : '0'} video=${needVideo ? '1' : '0'}`
    )
  } catch (_error) {
    setPermissionBootstrapState('failed')
    setPermissionBootstrapError(getPermissionHint())
    logCheckin(
      `permission bootstrap failed audio=${needAudio ? '1' : '0'} video=${needVideo ? '1' : '0'}`
    )
  }
}, [cleanupBootstrapMedia, logCheckin, loadDevices, requiredSteps])
```

#### 权限提示
```typescript
const getPermissionHint = () => {
  if (isSafariBrowser()) {
    return '请在 iPhone 设置 -> Safari -> 麦克风/相机 中允许访问，并在地址栏网站设置中允许权限后重试。'
  }
  return '请在手机系统设置和浏览器站点权限中允许麦克风/相机访问，然后重新申请权限。'
}
```

### 3. 设备枚举与选择

#### 设备加载
```typescript
const loadDevices = useCallback(async () => {
  if (!navigator.mediaDevices?.enumerateDevices) {
    return
  }

  try {
    const devices = await navigator.mediaDevices.enumerateDevices()

    const outputs = devices.filter(item => item.kind === 'audiooutput')
    const inputs = devices.filter(item => item.kind === 'audioinput')
    const cameras = devices.filter(item => item.kind === 'videoinput')

    setSpeakerDevices(outputs)
    setMicDevices(inputs)
    setCameraDevices(cameras)

    // 自动选中第一个设备
    setSelectedSpeaker(prev => prev || outputs[0]?.deviceId || '')
    setSelectedMic(prev => prev || inputs[0]?.deviceId || '')
    setSelectedCamera(prev => prev || cameras[0]?.deviceId || '')

    // 检测音频输出可见性（iOS Safari 会隐藏）
    if (outputs.length > 0) {
      setAudioOutputVisibility('visible')
    } else if (inputs.length > 0 || cameras.length > 0) {
      setAudioOutputVisibility('likely_hidden_by_platform')
    } else {
      setAudioOutputVisibility('unknown')
    }

    logCheckin(
      `enumerate devices outputs=${outputs.length} inputs=${inputs.length} cameras=${cameras.length}`
    )
  } catch (_error) {
    logCheckin('enumerate devices failed')
    setErrorMessage('无法读取设备列表，请检查浏览器权限。')
  }
}, [logCheckin])
```

### 4. 串行设备检测

#### 检测顺序
```
1. 扬声器 (speaker) -> 播放测试音
2. 麦克风 (mic) -> 波形可视化
3. 摄像头 (camera) -> 视频预览
4. 屏幕共享 (screen) -> 全屏验证
```

#### 扬声器测试
```typescript
const startSpeakerTest = async () => {
  setErrorMessage('')
  cleanupTestMedia()

  const AudioContextClass = getAudioContext()
  if (!AudioContextClass || !speakerCanvasRef.current) {
    setErrorMessage('当前浏览器不支持扬声器可视化测试。')
    return
  }

  try {
    // 创建测试音（620Hz 正弦波）
    const audioContext = new AudioContextClass()
    const oscillator = audioContext.createOscillator()
    const gainNode = audioContext.createGain()
    const analyser = audioContext.createAnalyser()
    const destination = audioContext.createMediaStreamDestination()
    const testAudio = new Audio()

    oscillator.type = 'sine'
    oscillator.frequency.value = 620
    gainNode.gain.value = 0.07
    oscillator.connect(gainNode)
    gainNode.connect(analyser)
    analyser.connect(destination)

    // 绑定到选中的扬声器
    testAudio.srcObject = destination.stream
    const testAudioWithSink = testAudio as HTMLAudioElement & {
      setSinkId?: (id: string) => Promise<void>
    }
    if (selectedSpeaker && testAudioWithSink.setSinkId) {
      await testAudioWithSink.setSinkId(selectedSpeaker)
    }

    await testAudio.play()

    testAudioRef.current = testAudio
    testAudioContextRef.current = audioContext
    testAnalyserRef.current = analyser
    setSpeakerPlaying(true)
    drawWave(analyser, speakerCanvasRef.current)

    // 自动停止（900ms）
    oscillator.start()
    window.setTimeout(() => {
      oscillator.stop()
      cleanupTestMedia()
      setSpeakerReady(true)
    }, 900)
  } catch (_error) {
    cleanupTestMedia()
    setErrorMessage('扬声器测试失败，请检查输出设备或浏览器设置。')
  }
}
```

#### 麦克风测试
```typescript
const startMicTest = async () => {
  setErrorMessage('')
  cleanupTestMedia()

  const AudioContextClass = getAudioContextClass()
  if (!AudioContextClass || !micCanvasRef.current) {
    setErrorMessage('当前浏览器不支持麦克风可视化测试。')
    return
  }

  try {
    // 获取麦克风流
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: selectedMic ? { deviceId: { exact: selectedMic } } : true,
      video: false
    })

    const audioContext = new AudioContextClass()
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 1024
    const source = audioContext.createMediaStreamSource(stream)
    source.connect(analyser)

    testMediaStreamRef.current = stream
    testAudioContextRef.current = audioContext
    testSourceRef.current = source
    testAnalyserRef.current = analyser
    setMicWaveActive(true)
    setMicReady(true)

    // 实时绘制波形
    drawWave(analyser, micCanvasRef.current)
  } catch (_error) {
    cleanupTestMedia()
    setMicReady(false)
    setErrorMessage('麦克风测试失败，请检查麦克风权限或设备。')
  }
}
```

#### 摄像头测试
```typescript
const startCameraPreview = async () => {
  setErrorMessage('')
  cleanupTestMedia()

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: selectedCamera ? { deviceId: { exact: selectedCamera } } : true
    })

    testMediaStreamRef.current = stream
    if (cameraVideoRef.current) {
      cameraVideoRef.current.srcObject = stream
      await cameraVideoRef.current.play()
    }

    setCameraReady(true)
  } catch (_error) {
    setCameraReady(false)
    setErrorMessage('摄像头预览失败，请检查摄像头权限或设备。')
  }
}
```

#### 屏幕共享测试
```typescript
const startScreenShareStep = async () => {
  setChecking(true)
  setErrorMessage('')

  if (!navigator.mediaDevices?.getDisplayMedia) {
    failStep('screen', '当前浏览器不支持屏幕共享，请使用电脑端 Chrome 或 Edge。')
    setChecking(false)
    return
  }

  if (isMobileBrowser()) {
    failStep('screen', '手机浏览器不支持"整个屏幕"共享，请改用电脑浏览器完成面试。')
    setChecking(false)
    return
  }

  try {
    // 请求屏幕共享（强制 monitor 类型）
    const displayMedia = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: 'monitor' },
      audio: false
    } as MediaStreamConstraints)

    const videoTrack = displayMedia.getVideoTracks()[0]
    const displaySurface = videoTrack?.getSettings().displaySurface

    if (displaySurface !== 'monitor') {
      stopStream(displayMedia)
      failStep('screen', '请共享"整个屏幕"，不要选择窗口或标签页。')
      return
    }

    // 保存到全局引用
    stopStream(mediaStreamsRef.current.displayMedia)
    mediaStreamsRef.current.displayMedia = displayMedia
    passStep('screen')
  } catch (_error) {
    failStep('screen', '屏幕共享失败，请允许并选择"整个屏幕"。')
  } finally {
    setChecking(false)
  }
}
```

### 5. 波形可视化

#### Canvas 绘制
```typescript
const drawWave = (analyser: AnalyserNode, canvas: HTMLCanvasElement) => {
  const context = canvas.getContext('2d')
  if (!context) return

  const dataArray = new Uint8Array(analyser.fftSize)

  const draw = () => {
    analyser.getByteTimeDomainData(dataArray)
    context.clearRect(0, 0, canvas.width, canvas.height)
    context.lineWidth = 2
    context.strokeStyle = '#7ed8b7'
    context.beginPath()

    const sliceWidth = canvas.width / dataArray.length
    let x = 0

    for (let i = 0; i < dataArray.length; i++) {
      const v = dataArray[i] / 128.0
      const y = (v * canvas.height) / 2

      if (i === 0) {
        context.moveTo(x, y)
      } else {
        context.lineTo(x, y)
      }
      x += sliceWidth
    }

    context.lineTo(canvas.width, canvas.height / 2)
    context.stroke()

    testAnimationRef.current = window.requestAnimationFrame(draw)
  }

  testAnimationRef.current = window.requestAnimationFrame(draw)
}
```

### 6. 进入面试前验证

#### 最终复检
```typescript
const validateBeforeEnter = async () => {
  // 1. 验证屏幕共享仍有效
  if (requiredSteps.includes('screen')) {
    const screenTrack = mediaStreamsRef.current.displayMedia
      ?.getVideoTracks()
      ?.at(0)
    if (!screenTrack || screenTrack.readyState !== 'live') {
      failStep('screen', '屏幕共享已关闭，请重新共享"整个屏幕"。')
      throw new Error('screen_failed')
    }
    const displaySurface = screenTrack.getSettings().displaySurface
    if (displaySurface !== 'monitor') {
      failStep('screen', '请确保当前仍在共享"整个屏幕"。')
      throw new Error('screen_failed')
    }
  }

  // 2. 验证扬声器
  if (requiredSteps.includes('speaker')) {
    await validateSpeakerAtEnter()
  }

  // 3. 重新获取麦克风/摄像头流
  const bootstrapStream = bootstrapMediaStreamRef.current
  let audioStream: MediaStream | null = null
  let videoStream: MediaStream | null = null

  if (requiredSteps.includes('mic')) {
    const audioTrack = bootstrapStream?.getAudioTracks()[0]
    if (audioTrack?.readyState === 'live') {
      audioStream = new MediaStream([audioTrack.clone()])
    } else {
      try {
        audioStream = await navigator.mediaDevices.getUserMedia({
          audio: selectedMic ? { deviceId: { exact: selectedMic } } : true,
          video: false
        })
      } catch (_error) {
        failStep('mic', '麦克风状态异常，请重新完成麦克风测试。')
        throw new Error('mic_failed')
      }
    }
  }

  if (requiredSteps.includes('camera')) {
    const videoTrack = bootstrapStream?.getVideoTracks()[0]
    if (videoTrack?.readyState === 'live') {
      videoStream = new MediaStream([videoTrack.clone()])
    } else {
      try {
        videoStream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: selectedCamera ? { deviceId: { exact: selectedCamera } } : true
        })
      } catch (_error) {
        stopStream(audioStream)
        failStep('camera', '摄像头状态异常，请重新完成摄像头测试。')
        throw new Error('camera_failed')
      }
    }
  }

  // 4. 合并音视频轨道并保存
  const tracks = [
    ...(audioStream?.getAudioTracks() || []),
    ...(videoStream?.getVideoTracks() || [])
  ]
  if (tracks.length > 0) {
    const mergedStream = new MediaStream(tracks)
    stopStream(mediaStreamsRef.current.userMedia)
    mediaStreamsRef.current.userMedia = mergedStream
  }

  stopStream(audioStream)
  stopStream(videoStream)
}
```

#### 扬声器二次验证
```typescript
const validateSpeakerAtEnter = async () => {
  // 检查设备仍存在
  let outputs: MediaDeviceInfo[] = []
  if (navigator.mediaDevices?.enumerateDevices) {
    const devices = await navigator.mediaDevices.enumerateDevices()
    outputs = devices.filter(item => item.kind === 'audiooutput')
  }

  if (
    outputs.length > 0 &&
    selectedSpeaker &&
    !outputs.find(item => item.deviceId === selectedSpeaker)
  ) {
    logCheckin('speaker validate failed selected speaker not found in output devices')
    failStep('speaker', '扬声器设备不可用，请重新完成扬声器测试。')
    throw new Error('speaker_failed')
  }

  // 快速播放测试音（220ms）
  const AudioContextClass = getAudioContextClass()
  if (!AudioContextClass) {
    failStep('speaker', '浏览器不支持扬声器校验，请更换浏览器。')
    throw new Error('speaker_failed')
  }

  const audioContext = new AudioContextClass()
  const oscillator = audioContext.createOscillator()
  const gainNode = audioContext.createGain()
  const destination = audioContext.createMediaStreamDestination()
  const testAudio = new Audio()

  oscillator.type = 'sine'
  oscillator.frequency.value = 560
  gainNode.gain.value = 0.03
  oscillator.connect(gainNode)
  gainNode.connect(destination)
  testAudio.srcObject = destination.stream

  const testAudioWithSink = testAudio as HTMLAudioElement & {
    setSinkId?: (id: string) => Promise<void>
  }
  if (selectedSpeaker && testAudioWithSink.setSinkId) {
    await testAudioWithSink.setSinkId(selectedSpeaker)
  }

  try {
    await testAudio.play()
  } catch (_error) {
    logCheckin('speaker validate play failed')
    failStep('speaker', '扬声器校验播放失败，请检查系统音量或输出设备。')
    await audioContext.close()
    throw new Error('speaker_failed')
  }

  oscillator.start()
  await new Promise(resolve => window.setTimeout(resolve, 220))
  oscillator.stop()
  testAudio.pause()
  testAudio.srcObject = null
  await audioContext.close()
}
```

#### 进入面试
```typescript
const handleEnter = async () => {
  if (!allGranted(permissions, requiredSteps)) {
    setCheckInPassed(false)
    setErrorMessage('请完成当前面试配置的设备检查后再进入面试。')
    return
  }

  setChecking(true)
  setErrorMessage('')

  try {
    if (!bootstrapReady) {
      throw new Error('权限尚未初始化完成，请先完成权限初始化。')
    }

    await validateBeforeEnter()

    // 保存选中的麦克风 ID（用于面试页重新获取）
    setSelectedMicId(selectedMic.trim())

    // 释放预检流（面试页会重新获取）
    stopStream(mediaStreamsRef.current.userMedia)
    mediaStreamsRef.current.userMedia = null
    logCheckin('release precheck userMedia before enter')

    setCheckInPassed(true)
    navigate(`/${location.search}`)
  } catch (error) {
    setCheckInPassed(false)
    setErrorMessage(
      error instanceof Error
        ? error.message
        : '设备状态检查未通过，请重新确认设备。'
    )
  } finally {
    setChecking(false)
  }
}
```

## 状态管理

### 全局状态（useSessionAuth）
```typescript
const {
  token,
  setCheckInPassed,
  setSelectedMicId,
  permissions,
  setPermissions,
  mediaStreamsRef
} = useSessionAuth()
```

### 配置加载状态
```typescript
const [loadingCheckinConfig, setLoadingCheckinConfig] = useState(true)
const [requiredSteps, setRequiredSteps] = useState<CheckStep[]>([...DEFAULT_REQUIRED_STEPS])
```

### 权限引导状态
```typescript
type PermissionBootstrapState = 'idle' | 'requesting' | 'granted' | 'failed'

const [permissionBootstrapState, setPermissionBootstrapState] =
  useState<PermissionBootstrapState>('idle')
const [permissionBootstrapError, setPermissionBootstrapError] = useState('')
```

### 设备列表
```typescript
const [speakerDevices, setSpeakerDevices] = useState<MediaDeviceInfo[]>([])
const [micDevices, setMicDevices] = useState<MediaDeviceInfo[]>([])
const [cameraDevices, setCameraDevices] = useState<MediaDeviceInfo[]>([])
const [selectedSpeaker, setSelectedSpeaker] = useState('')
const [selectedMic, setSelectedMic] = useState('')
const [selectedCamera, setSelectedCamera] = useState('')
```

### 检测流程状态
```typescript
const [currentStepIndex, setCurrentStepIndex] = useState(0)
const [errorMessage, setErrorMessage] = useState('')
const [checking, setChecking] = useState(false)
const [modalType, setModalType] = useState<ModalType>(null)
const [speakerReady, setSpeakerReady] = useState(false)
const [micReady, setMicReady] = useState(false)
const [cameraReady, setCameraReady] = useState(false)
```

### 媒体流引用
```typescript
const testAnimationRef = useRef<number | null>(null)
const testAudioContextRef = useRef<AudioContext | null>(null)
const testSourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
const testAnalyserRef = useRef<AnalyserNode | null>(null)
const testAudioRef = useRef<HTMLAudioElement | null>(null)
const testMediaStreamRef = useRef<MediaStream | null>(null)
const bootstrapMediaStreamRef = useRef<MediaStream | null>(null)
```

## UI 布局

### 页面结构
```tsx
<main className="gate-page">
  <section className="gate-card">
    <h1>面试设备检查</h1>

    {/* 配置加载中 */}
    {loadingCheckinConfig && <p>正在加载本场面试检查项...</p>}

    {/* 必检项说明 */}
    {!loadingCheckinConfig && requiredSteps.length > 0 && (
      <p>
        请按顺序完成：
        {requiredSteps.map((step, index) => (
          <span key={step}>
            {index > 0 ? ' -> ' : ' '}
            {STEP_LABEL[step]}
          </span>
        ))}
        。
      </p>
    )}

    {/* 权限初始化状态 */}
    <p className="gate-hint">
      权限初始化状态：
      {permissionBootstrapState === 'requesting' && '初始化中...'}
      {permissionBootstrapState === 'granted' && '已完成'}
      {permissionBootstrapState === 'failed' && '失败'}
      {permissionBootstrapState === 'idle' && '未开始'}
    </p>

    {/* 权限错误提示 */}
    {permissionBootstrapError && (
      <p className="gate-error">{permissionBootstrapError}</p>
    )}

    {/* 检测项列表 */}
    <ul className="permission-list">
      {requiredSteps.map((step, index) => {
        const status = permissions[step]
        const isCurrent = index === currentStepIndex
        const isLocked = index > currentStepIndex

        return (
          <li className="permission-item" key={step}>
            <span>{STEP_LABEL[step]}</span>
            <div className="permission-right">
              <strong className={`permission-status is-${status}`}>
                {status === 'granted' ? '已通过' : statusTextMap[status]}
              </strong>
              {!isLocked && (
                <button
                  type="button"
                  className="permission-test-btn"
                  disabled={checking}
                  onClick={() => openStepTest(step)}
                >
                  {step === 'screen'
                    ? '共享全屏'
                    : isCurrent
                      ? '测试'
                      : '已完成'}
                </button>
              )}
            </div>
          </li>
        )
      })}
    </ul>

    {/* 错误提示 */}
    {errorMessage && <p className="gate-error">{errorMessage}</p>}

    {/* 当前步骤 */}
    {!errorMessage && !loadingCheckinConfig && requiredSteps.length > 0 && (
      <p className="gate-hint">
        当前步骤：{currentStep ? STEP_LABEL[currentStep] : '已完成'}
      </p>
    )}

    {/* 操作按钮 */}
    <div className="gate-actions">
      {requiredSteps.length > 0 && (
        <button
          type="button"
          className="gate-btn is-secondary"
          onClick={bootstrapPermissions}
          disabled={permissionBootstrapState === 'requesting'}
        >
          {permissionBootstrapState === 'requesting'
            ? '权限初始化中...'
            : '重新申请权限'}
        </button>
      )}
      <button
        type="button"
        className="gate-btn is-secondary"
        onClick={loadDevices}
      >
        刷新设备列表
      </button>
      <button
        type="button"
        className="gate-btn is-secondary"
        onClick={resetAll}
      >
        重新检查
      </button>
      <button
        type="button"
        className="gate-btn is-enter"
        onClick={handleEnter}
        disabled={
          loadingCheckinConfig ||
          checking ||
          !canEnter ||
          permissionBootstrapState !== 'granted'
        }
      >
        {checking ? '校验中...' : '进入面试'}
      </button>
    </div>

    {/* iOS 提示 */}
    {audioOutputVisibility === 'likely_hidden_by_platform' && (
      <p className="gate-hint">
        当前浏览器未暴露输出设备列表（常见于 iOS），将使用系统默认扬声器进行测试。
      </p>
    )}
  </section>

  {/* 测试模态框 */}
  {modalType && (
    <div className="test-modal-mask" role="presentation">
      <section className="test-modal" role="dialog" aria-modal="true">
        {/* 设备选择 + 测试按钮 + 波形/预览 */}
      </section>
    </div>
  )}
</main>
```

### 关键样式类
- `gate-page`：主容器
- `gate-card`：卡片容器
- `gate-hint`：提示文本
- `gate-error`：错误提示
- `permission-list`：检测项列表
- `permission-item`：单项容器
- `permission-status`：状态标签
- `permission-test-btn`：测试按钮
- `gate-actions`：按钮组
- `gate-btn`：通用按钮
- `test-modal-mask`：模态框遮罩
- `test-modal`：模态框内容
- `mic-wave-canvas`：波形画布
- `camera-preview`：摄像头预览

## 依赖与配置

### 核心依赖
```typescript
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from '@modern-js/runtime/router'
import { useSessionAuth } from '@/auth/context'
import { API_URL } from '@/config/endpoints'
import type { PermissionKey, PermissionState, PermissionStatus } from '@/auth/types'
import { logSender } from '@/utils/log_sender'
```

### 浏览器 API
- `navigator.mediaDevices.getUserMedia`：获取麦克风/摄像头
- `navigator.mediaDevices.getDisplayMedia`：获取屏幕共享
- `navigator.mediaDevices.enumerateDevices`：枚举设备列表
- `AudioContext`：音频处理和波形绘制
- `HTMLAudioElement.setSinkId`：切换输出设备

### 权限要求
- **HTTPS 或 localhost**：媒体 API 仅在安全上下文可用
- **浏览器权限**：需要用户授予麦克风/摄像头/屏幕共享权限
- **系统权限**（移动端）：需要在系统设置中允许浏览器访问硬件

## 日志与排障

### 日志输出
```typescript
const logCheckin = useCallback(
  (message: string) => {
    const entry = `[${new Date().toLocaleTimeString()}]\t[CheckIn] ${message}`
    logSender.enqueue(entry, token)
  },
  [token]
)

// 关键日志点
- enumerate devices outputs=${count} inputs=${count} cameras=${count}
- permission bootstrap start required_steps=${steps}
- permission bootstrap granted audio=${0/1} video=${0/1}
- permission bootstrap failed audio=${0/1} video=${0/1}
- release precheck userMedia before enter
```

### 常见故障与排查步骤

#### 1. 一直停留在"待检测"
**现象**：权限状态始终为 pending

**排查**：
- 检查浏览器是否弹出权限对话框
- 检查是否误点了"拒绝"或"不再询问"
- 检查系统设置中是否允许浏览器访问硬件

**解决**：
- 点击地址栏权限图标，重新授权
- 清除站点数据并刷新页面
- iOS Safari：设置 -> Safari -> 麦克风/相机 -> 允许

#### 2. 屏幕共享总失败
**现象**：提示"请共享'整个屏幕'"

**原因**：
- 选择了窗口或标签页（displaySurface !== 'monitor'）
- 移动浏览器不支持屏幕共享
- 浏览器不支持 getDisplayMedia

**解决**：
- 确保选择"整个屏幕"选项（不是"应用窗口"或"Chrome 标签页"）
- 使用桌面端 Chrome/Edge 浏览器
- 检查浏览器版本是否支持 Screen Capture API

#### 3. 进入面试按钮置灰
**现象**：无法点击"进入面试"按钮

**排查**：
```typescript
console.log('canEnter:', canEnter)
console.log('permissions:', permissions)
console.log('requiredSteps:', requiredSteps)
console.log('bootstrapReady:', permissionBootstrapState === 'granted')
```

**解决**：
- 确保所有必检项状态为 `granted`
- 确保权限初始化完成（`bootstrapReady === true`）
- 检查是否有未完成的检测项

#### 4. 设备列表为空
**现象**：下拉框显示"系统默认输出"

**原因**：
- 权限未授予（设备信息被隐藏）
- iOS Safari 隐藏音频输出设备列表
- 系统无可用设备

**解决**：
- 完成权限初始化后自动重新枚举
- iOS：使用系统默认扬声器
- 检查系统设备管理器中设备状态

#### 5. 波形无响应
**现象**：麦克风/扬声器测试时波形不动

**排查**：
- 检查 AudioContext 状态：`audioContext.state`
- 检查 AnalyserNode 是否正确连接
- 检查麦克风是否静音或音量过低

**解决**：
```typescript
// 验证音频链路
console.log('AudioContext state:', testAudioContextRef.current?.state)
console.log('AnalyserNode:', testAnalyserRef.current)
console.log('MediaStream active:', testMediaStreamRef.current?.active)
```

#### 6. 摄像头预览黑屏
**现象**：视频元素显示但画面全黑

**排查**：
- 检查 video.srcObject 是否正确设置
- 检查 MediaStream 轨道状态：`track.readyState`
- 检查摄像头是否被其他应用占用

**解决**：
```typescript
// 验证视频轨道
const videoTrack = cameraVideoRef.current?.srcObject?.getVideoTracks()[0]
console.log('Video track:', videoTrack)
console.log('Track state:', videoTrack?.readyState)
console.log('Track enabled:', videoTrack?.enabled)
```

## 手工验证

### 完整测试流程

#### 1. 访问检查页
- 打开 `/check-in?token={valid_token}`
- 确认页面加载并显示必检项

#### 2. 权限初始化
- 点击"重新申请权限"
- 浏览器弹出权限对话框
- 允许麦克风和摄像头访问
- 确认状态变为"已完成"

#### 3. 扬声器测试
- 点击"扬声器"行的"测试"按钮
- 打开测试模态框
- 选择输出设备（如有多个）
- 点击"播放测试音"
- 确认听到 620Hz 测试音
- 观察波形线条波动
- 点击"我听到了声音"
- 确认状态变为"已通过"

#### 4. 麦克风测试
- 点击"麦克风"行的"测试"按钮
- 打开测试模态框
- 选择输入设备（如有多个）
- 点击"开始测试"
- 对着麦克风说话
- 观察波形随声音波动
- 确认波形正常后点击"波形正常，继续"
- 确认状态变为"已通过"

#### 5. 摄像头测试（如配置）
- 点击"摄像头"行的"测试"按钮
- 打开测试模态框
- 选择摄像头设备
- 点击"开始预览"
- 确认预览画面正常
- 点击"画面正常，继续"
- 确认状态变为"已通过"

#### 6. 屏幕共享测试（如配置）
- 点击"屏幕共享"行的"共享全屏"按钮
- 系统弹出共享选择对话框
- 选择"整个屏幕"（不要选窗口或标签页）
- 点击"共享"
- 确认状态变为"已通过"

#### 7. 进入面试
- 确认所有必检项为"已通过"
- 点击"进入面试"按钮
- 确认跳转到面试页面

#### 8. 边界测试
- 测试中途关闭麦克风/摄像头权限
- 测试屏幕共享中途停止共享
- 测试设备拔出后重新检测
- 测试权限拒绝后重新申请
- 测试无效 token 访问

#### 9. 移动端测试
- iOS Safari：确认权限提示准确
- Android Chrome：确认设备枚举正常
- 确认移动端隐藏屏幕共享选项

## 相关文档
- [会话认证上下文](../auth/context.md)
- [面试管理后台](./admin-interviews.md)
- [面试主页面](./index.md)
- [日志上报](../utils/log_sender.md)
