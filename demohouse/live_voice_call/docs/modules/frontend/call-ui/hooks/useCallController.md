# frontend/src/call-ui/hooks/useCallController.ts

## 模块概述
useCallController 是通话页面的核心控制器 Hook，负责统一管理 UI 状态、用户操作响应、模式切换、自动连接、自动结束流程和资源清理。它是 CallInterviewPage 与底层服务之间的桥梁，协调多个子 Hook 的状态并提供统一的控制接口。

## 模块职责
- 统一管理通话页面的所有 UI 状态
- 处理控制栏的用户操作（麦克风、挂断等）
- 实现 real（真实后端）和 mock（模拟模式）双模式
- 自动连接守护（real 模式下自动尝试连接）
- 自动结束流程（连接断开触发倒计时跳转）
- 计时器管理（通话时长统计）
- 资源释放（停止录音、关闭媒体流、复位状态）
- 调试面板控制（仅开发环境）

## 入口与调用方
由 `CallInterviewPage` 组件使用，提供完整的 `CallController` 对象供页面渲染和交互使用。

## 对外接口（导出项）

### useCallController Hook
```typescript
export const useCallController = (): CallController
```

返回值 `CallController` 包含：
```typescript
{
  uiState: {
    mode: CallMode;                    // 'real' | 'mock'
    isConnected: boolean;              // WebSocket 连接状态
    isInCall: boolean;                 // 通话中状态
    interviewerSpeaking: boolean;      // 面试官是否在说话
    candidateSpeaking: boolean;        // 候选人是否在说话
    micOn: boolean;                    // 麦克风开启状态
    camOn: boolean;                    // 摄像头开启状态
    shareOn: boolean;                  // 屏幕共享开启状态
    elapsedSec: number;                // 通话时长（秒）
    subtitle: string;                  // 实时字幕
    endNotice?: string;                // 结束通知消息
    interviewerAudioLevel: number;     // 面试官音量 (0-1)
    userAudioLevel: number;            // 候选人音量 (0-1)
    interviewer: Participant;          // 面试官信息
    user: Participant;                 // 候选人信息
  };
  debugState: DebugState;              // 调试状态
  transcripts: TranscriptItem[];       // 对话记录
  debugAllowed: boolean;               // 是否允许调试
  debugOpen: boolean;                  // 调试面板是否打开
  messagePanelOpen: boolean;           // 消息面板是否打开
  setWsUrl: (url: string) => void;     // 设置 WebSocket URL
  onControlAction: (action: CallControlAction) => void;  // 处理控制操作
}
```

### formatDuration 工具函数
```typescript
export const formatDuration = (seconds: number): string
```
将秒数格式化为 MM:SS 格式的时间字符串。

## 核心状态管理

### 模式状态
```typescript
const [mode, setMode] = useState<CallMode>('real');
```
- **real**: 真实模式，连接后端 WebSocket 服务
- **mock**: 模拟模式，使用本地脚本模拟对话流程

### 调试状态
```typescript
const [debugOpen, setDebugOpen] = useState(false);
const [messagePanelOpen, setMessagePanelOpen] = useState(false);
const debugAllowed = useMemo(() => isDebugAllowed(location.search), [location.search]);
```
- debugAllowed 仅在 `localhost` 且 URL 含 `token=DEBUG` 时为 true

### 设备状态
```typescript
const [camOn, setCamOn] = useState(false);
const [shareOn, setShareOn] = useState(false);
```
- 摄像头和屏幕共享的开启状态（当前版本仅 UI 状态，未实际启用）

### Mock 模式状态
```typescript
const [mockConnected, setMockConnected] = useState(false);
const [mockInCall, setMockInCall] = useState(false);
const [mockMicOn, setMockMicOn] = useState(false);
const [mockUserSentence, setMockUserSentence] = useState('');
const [mockBotSentence, setMockBotSentence] = useState('');
const [mockLogs, setMockLogs] = useState<string[]>([]);
const [mockMessages, setMockMessages] = useState<TranscriptItem[]>([]);
```

### 计时器状态
```typescript
const [elapsedSec, setElapsedSec] = useState(0);
```
- 每秒递增，用于显示通话时长
- 仅在 real 模式且未结束时运行

### 结束流程状态
```typescript
type EndPhase = 'idle' | 'waiting_last_audio' | 'countdown';
const [endPhase, setEndPhase] = useState<EndPhase>('idle');
const [endCountdownSec, setEndCountdownSec] = useState<number | null>(null);
```

**结束阶段说明**:
- **idle**: 正常通话中，无结束流程
- **waiting_last_audio**: 等待最后一段 TTS 音频播放完毕
- **countdown**: 倒计时中，显示倒计时提示并自动跳转

## 自动结束机制详解

### 触发条件
在 real 模式下，WebSocket 连接从 true 变为 false（断连）时自动触发。

### 完整流程

```
WebSocket 断连
    ↓
startAutoFinishFlow() 被调用
    ↓
检查 botAudioPlaying 或 botSpeaking
    ↓
┌──────────────────────┬──────────────────────┐
│  有音频正在播放      │  无音频播放          │
│  endPhase = waiting  │  endPhase = countdown│
│  倒计时 = null       │  倒计时 = 15秒       │
└──────────────────────┴──────────────────────┘
              ↓                    ↓
        音频播放结束          倒计时递减
              ↓                    ↓
        endPhase = countdown    倒计时归零
              ↓                    ↓
        倒计时 = 15秒              ↓
              ↓                    ↓
              └────────────────────┘
                       ↓
            navigate('/hangup-result')
```

### 核心代码解析

**startAutoFinishFlow 函数**:
```typescript
const startAutoFinishFlow = useCallback(() => {
  if (endingRef.current) return;  // 防止重复触发

  endingRef.current = true;        // 标记已进入结束流程
  connectingRef.current = false;   // 停止连接守护
  cleanupCaptureResources();       // 清理录音和媒体流
  disconnectSession();             // 断开 WebSocket

  // 如果 TTS 正在播放，等待播放完成
  if (botAudioPlaying || botSpeaking) {
    setEndPhase('waiting_last_audio');
    setEndCountdownSec(null);
    return;
  }

  // 否则直接进入倒计时
  setEndPhase('countdown');
  setEndCountdownSec(AUTO_REDIRECT_SECONDS);  // 15秒
}, [botAudioPlaying, botSpeaking, cleanupCaptureResources, disconnectSession]);
```

**监听音频播放结束**:
```typescript
useEffect(() => {
  if (mode !== 'real' || endPhase !== 'waiting_last_audio') return;

  // 音频播放完毕，启动倒计时
  if (!botAudioPlaying && !botSpeaking) {
    setEndPhase('countdown');
    setEndCountdownSec(AUTO_REDIRECT_SECONDS);
  }
}, [botAudioPlaying, botSpeaking, endPhase, mode]);
```

**倒计时跳转**:
```typescript
useEffect(() => {
  if (endPhase !== 'countdown' || endCountdownSec === null) return;

  if (endCountdownSec <= 0) {
    navigate(`/hangup-result${location.search}`);
    return;
  }

  const timer = window.setTimeout(() => {
    setEndCountdownSec(prev => prev === null ? null : prev - 1);
  }, 1000);

  return () => window.clearTimeout(timer);
}, [endCountdownSec, endPhase, location.search, navigate]);
```

### finishImmediately 函数
用于用户主动挂断时立即结束，跳过等待和倒计时：
```typescript
const finishImmediately = useCallback(() => {
  endingRef.current = true;
  connectingRef.current = false;
  setEndPhase('idle');
  setEndCountdownSec(null);
  cleanupCaptureResources();
  shutdownSession();              // 完全关闭会话（包括 AudioContext）
  navigate(`/hangup-result${location.search}`);
}, [cleanupCaptureResources, location.search, navigate, shutdownSession]);
```

## 自动连接守护

### 连接守护逻辑
在 real 模式下，如果未连接且未结束，自动尝试连接：
```typescript
useEffect(() => {
  if (mode !== 'real' || wsConnected || endingRef.current || connectingRef.current) {
    return;
  }

  connectingRef.current = true;
  handleConnect();

  // 1.2秒后重置连接标记（防止重复连接）
  const guardTimer = window.setTimeout(() => {
    connectingRef.current = false;
  }, 1200);

  return () => window.clearTimeout(guardTimer);
}, [handleConnect, mode, wsConnected]);
```

### 连接状态监听
监听 WebSocket 连接状态变化，触发自动结束：
```typescript
useEffect(() => {
  if (mode !== 'real') {
    prevWsConnectedRef.current = wsConnected;
    return;
  }

  const wasConnected = prevWsConnectedRef.current;

  // 从已连接变为未连接，且未在结束流程中
  if (wasConnected && !wsConnected && !endingRef.current) {
    startAutoFinishFlow();
  }

  prevWsConnectedRef.current = wsConnected;
}, [mode, startAutoFinishFlow, wsConnected]);
```

## 控制操作处理

### CallControlAction 类型
```typescript
type CallControlAction =
  | 'toggleMic'
  | 'toggleCam'
  | 'toggleShare'
  | 'hangUp'
  | 'connect'
  | 'toggleDebug'
  | 'toggleMessagePanel'
  | 'switchMode';
```

### onControlAction 函数详解

**toggleMic（切换麦克风）**:
```typescript
case 'toggleMic':
  if (!isConnected) {
    Message.warning('请先连接');
    return;
  }

  if (mode === 'mock') {
    const next = !mockMicOn;
    setMockMicOn(next);
    setMockInCall(next);
    if (next) setElapsedSec(0);
    else {
      setMockUserSentence('');
      setMockBotSentence('');
    }
    return;
  }

  // Real 模式：切换录音状态
  if (userSpeaking) {
    recStop();
  } else {
    recStart();
  }
```

**hangUp（挂断）**:
```typescript
case 'hangUp':
  if (mode === 'mock') {
    // Mock 模式清理
    setMockInCall(false);
    setMockMicOn(false);
    setMockUserSentence('');
    setMockBotSentence('');
    setUserSpeaking(false);
    setElapsedSec(0);
    if (mockPlaybackTimerRef.current) {
      window.clearInterval(mockPlaybackTimerRef.current);
      mockPlaybackTimerRef.current = null;
    }
    navigate(`/hangup-result${location.search}`);
    return;
  }

  // Real 模式：立即结束
  finishImmediately();
```

**switchMode（切换模式）**:
```typescript
case 'switchMode':
  setMode(prev => prev === 'mock' ? 'real' : 'mock');
  setElapsedSec(0);
  setBotAudioLevel(0);
  setUserAudioLevel(0);
  setEndPhase('idle');
  setEndCountdownSec(null);
  endingRef.current = false;
  connectingRef.current = false;
```

## 资源清理机制

### cleanupCaptureResources 函数
清理所有采集相关的资源：
```typescript
const cleanupCaptureResources = useCallback(() => {
  recStop();                    // 停止录音
  releaseSessionMedia();        // 释放媒体流
  setBotAudioLevel(0);          // 重置音量
  setUserAudioLevel(0);
  setCamOn(false);              // 重置设备状态
  setShareOn(false);
  setElapsedSec(0);             // 重置计时器
}, [recStop, releaseSessionMedia, setBotAudioLevel, setUserAudioLevel]);
```

### releaseSessionMedia 函数
释放会话媒体流：
```typescript
const releaseSessionMedia = useCallback(() => {
  stopStream(mediaStreamsRef.current.userMedia);
  stopStream(mediaStreamsRef.current.displayMedia);
  mediaStreamsRef.current.userMedia = null;
  mediaStreamsRef.current.displayMedia = null;
}, [mediaStreamsRef, stopStream]);
```

### stopStream 工具函数
停止媒体流的所有轨道：
```typescript
const stopStream = useCallback((stream: MediaStream | null) => {
  if (!stream) return;

  for (const track of stream.getTracks()) {
    track.stop();
  }
}, []);
```

## Mock 模式详解

### 默认脚本
```typescript
const DEFAULT_SCRIPT: MockCallScript[] = [
  {
    userSentence: '你好，我准备好了，可以开始今天的面试吗？',
    botSentence: '当然可以。先做一个一分钟的自我介绍吧。',
  },
  {
    userSentence: '我擅长前端工程化，也参与过 AI 应用交互设计。',
    botSentence: '很好。你如何在实时语音场景里平衡延迟、可读性和可维护性？',
  },
];
```

### Mock 对话模拟流程
```typescript
useEffect(() => {
  if (mode !== 'mock' || !mockInCall) return;

  const script = DEFAULT_SCRIPT[mockScriptIndexRef.current % DEFAULT_SCRIPT.length];
  mockScriptIndexRef.current += 1;

  // 1. 模拟候选人发言
  setMockUserSentence(script.userSentence);
  setMockBotSentence('');
  setUserSpeaking(true);

  // 2. 900ms 后识别完成
  window.setTimeout(() => {
    setUserSpeaking(false);
    const scriptMessages = transcriptFromScript(script);
    setMockMessages(prev => [...prev, ...scriptMessages]);

    // 3. 逐字显示面试官回复
    let index = 0;
    const fullText = script.botSentence;
    mockPlaybackTimerRef.current = window.setInterval(() => {
      index += 1;
      setMockBotSentence(fullText.slice(0, index));

      if (index >= fullText.length) {
        window.clearInterval(mockPlaybackTimerRef.current);
        mockPlaybackTimerRef.current = null;
      }
    }, 30);  // 每30ms显示一个字符
  }, 900);
}, [mode, mockInCall, setUserSpeaking]);
```

## 依赖的 Hook

### AudioChatProvider 相关
```typescript
const {
  wsConnected,          // WebSocket 连接状态
  botSpeaking,          // Bot 正在合成语音
  userSpeaking,         // 用户正在说话
  botAudioPlaying,      // Bot 音频正在播放
  botAudioLevel,        // Bot 音量等级
  audioUnlocked,        // AudioContext 是否已解锁
  audioRouteMode,       // 音频路由模式
  userAudioLevel,       // 用户音量等级
  setUserSpeaking,
  setBotAudioLevel,
  setUserAudioLevel,
} = useAudioChatState();

const { chatMessages } = useMessageList();
```

### AudioChatServiceProvider 相关
```typescript
const { currentBotSentence, currentUserSentence } = useCurrentSentence();
const { wsUrl, setWsUrl } = useWsUrl();
const { recStart, recStop } = useAudioRecorder();
const { handleConnect, disconnectSession, shutdownSession } = useVoiceBotService();
const { logContent } = useLogContent();
```

### SessionAuth 相关
```typescript
const { mediaStreamsRef } = useSessionAuth();
```

## 计算属性（useMemo）

### subtitle（实时字幕）
```typescript
const subtitle = useMemo(() => {
  if (mode === 'mock') {
    return mockBotSentence || mockUserSentence;
  }
  return currentBotSentence || currentUserSentence;
}, [mode, mockBotSentence, mockUserSentence, currentBotSentence, currentUserSentence]);
```

### endNotice（结束提示）
```typescript
const endNotice = useMemo(() => {
  if (mode !== 'real') return undefined;

  if (endPhase === 'countdown' && endCountdownSec !== null) {
    return `面试已结束，${endCountdownSec} 秒后自动跳转结果页`;
  }

  return undefined;
}, [endCountdownSec, endPhase, mode]);
```

### 其他计算状态
```typescript
const realInCall = userSpeaking || botSpeaking || botAudioPlaying;
const isConnected = mode === 'mock' ? mockConnected : wsConnected;
const isInCall = mode === 'mock' ? mockInCall : realInCall;
const interviewerSpeaking = mode === 'mock'
  ? mockBotSentence.length > 0
  : botSpeaking || botAudioPlaying;
const candidateSpeaking = mode === 'mock'
  ? mockUserSentence.length > 0 && mockBotSentence.length === 0
  : userSpeaking;
const micOn = mode === 'mock' ? mockMicOn : userSpeaking;
const transcripts = mode === 'mock' ? mockMessages : toTranscriptItems(chatMessages);
const logs = mode === 'mock' ? mockLogs : logContent;
```

## 使用示例

```typescript
import { useCallController } from '@/call-ui/hooks/useCallController';

export const CallInterviewPage = () => {
  const controller = useCallController();
  const { uiState, debugState, onControlAction } = controller;

  return (
    <main className="call-page">
      <header>
        <span className={`status-pill ${uiState.isConnected ? 'is-online' : 'is-offline'}`}>
          {uiState.isConnected ? '已连接' : '未连接'}
        </span>
        <span>{formatDuration(uiState.elapsedSec)}</span>
      </header>

      {uiState.endNotice && <div>{uiState.endNotice}</div>}

      <CallControlBar
        isInCall={uiState.isInCall}
        onAction={onControlAction}
      />
    </main>
  );
};
```

## 日志与排障

### 日志来源
- Mock 模式：`mockLogs` 状态数组
- Real 模式：`useLogContent` Hook 提供的 `logContent`
- 可通过 DebugDrawer 查看实时日志

### 关键观察点
1. **连接守护**: connectingRef 防止重复连接
2. **结束流程**: endingRef 防止重复触发
3. **状态转换**: endPhase 的变化（idle -> waiting -> countdown）
4. **倒计时**: endCountdownSec 的递减
5. **资源清理**: cleanupCaptureResources 的执行

## 常见故障与排查步骤

### 1. 连接后立即跳转结果页
**现象**: 进入通话页后几秒内就自动跳转到结果页

**排查步骤**:
1. 检查 `wsConnected` 状态变化日志
2. 确认是否短暂为 true 后又变 false
3. 检查后端 WebSocket 是否主动断开
4. 验证网络连接稳定性
5. 查看后端日志确认断连原因

**可能原因**:
- 后端服务异常主动断开连接
- 网络抖动导致连接不稳定
- Token 无效或过期
- 后端初始化失败

### 2. 挂断后摄像头/麦克风未释放
**现象**: 点击挂断后，浏览器标签页仍显示摄像头/麦克风正在使用

**排查步骤**:
1. 检查 `cleanupCaptureResources` 是否被调用
2. 验证 `releaseSessionMedia` 是否执行
3. 确认 `mediaStreamsRef` 中的 track 是否调用 stop()
4. 检查是否有其他代码持有媒体流引用

**解决方法**:
- 确保 finishImmediately 或 startAutoFinishFlow 正确调用
- 手动检查所有 MediaStream 引用并调用 stop()

### 3. Debug 面板不可见
**现象**: 点击调试按钮无反应，或调试按钮不显示

**排查条件**:
1. 必须在 `localhost` 环境运行
2. URL 必须包含 `token=DEBUG` 参数
3. 示例: `http://localhost:8080/?token=DEBUG`

**验证方法**:
```typescript
// 在控制台检查
console.log(window.location.hostname);  // 应为 'localhost'
console.log(new URLSearchParams(window.location.search).get('token'));  // 应为 'DEBUG'
```

### 4. 计时器不运行或不准确
**现象**: 通话时长不递增或跳跃

**排查步骤**:
1. 确认 mode === 'real'
2. 检查 endingRef.current 是否为 false
3. 验证 useEffect 的 timer 是否正常运行
4. 确认组件未频繁卸载/重载

### 5. Mock 模式对话不循环
**现象**: Mock 模式下只执行一轮对话就停止

**原因**: Mock 模式需要用户主动点击麦克风来触发下一轮
- 点击麦克风 → mockInCall 变化 → 触发 useEffect → 播放下一个脚本

### 6. 自动结束流程卡在 waiting_last_audio
**现象**: 连接断开后一直显示 waiting 状态，不进入倒计时

**排查步骤**:
1. 检查 botAudioPlaying 和 botSpeaking 状态
2. 确认这两个状态是否能正确变为 false
3. 验证音频播放完成事件是否触发
4. 检查 VoiceBotService 的 onStopPlayAudio 回调

### 7. 切换模式后状态异常
**现象**: 从 real 切换到 mock（或反之）后状态混乱

**解决方法**:
- switchMode 动作已重置关键状态
- 如需更彻底重置，可考虑刷新页面或导航到 check-in 页

## 性能优化建议

### 1. 减少不必要的 re-render
- 已使用 useCallback 包裹所有回调函数
- 已使用 useMemo 计算派生状态
- 避免在 onControlAction 中创建新对象

### 2. 定时器管理
- 使用 window.setTimeout/setInterval 而非 setInterval
- 在 useEffect cleanup 中清理定时器
- 使用 ref 存储定时器 ID

### 3. 状态更新优化
- 使用函数式更新 `setState(prev => prev + 1)`
- 批量更新相关状态
- 避免在循环中调用 setState

## 手工验证清单

### Real 模式验证
- [ ] 页面加载后自动尝试连接
- [ ] 连接成功后状态变为"已连接"
- [ ] 点击麦克风开始录音
- [ ] 识别结果实时显示在字幕中
- [ ] 面试官回复正常播放
- [ ] 计时器正常计时
- [ ] 点击挂断正确跳转
- [ ] 断连后自动进入结束流程
- [ ] 倒计时正确显示并跳转

### Mock 模式验证
- [ ] 切换到 mock 模式
- [ ] 点击连接按钮连接成功
- [ ] 点击麦克风触发模拟对话
- [ ] 字幕逐字显示模拟内容
- [ ] 消息面板显示对话记录
- [ ] 点击挂断正确跳转
- [ ] 日志面板显示模拟日志

### 调试功能验证
- [ ] 在 localhost + token=DEBUG 下调试按钮显示
- [ ] 点击调试按钮打开 DebugDrawer
- [ ] 可以切换 real/mock 模式
- [ ] 可以修改 WebSocket URL
- [ ] 消息面板正确显示对话记录
- [ ] 日志面板实时更新

## 相关文档
- [CallInterviewPage 页面文档](../CallInterviewPage.md)
- [AudioChatProvider 状态管理](../../components/AudioChatProvider/index.md)
- [AudioChatServiceProvider 服务管理](../../components/AudioChatServiceProvider/index.md)
- [useVoiceBotService Hook](../../components/AudioChatServiceProvider/hooks/useVoiceBotService.md)
- [useAudioRecorder Hook](../../components/AudioChatServiceProvider/hooks/useAudioRecorder.md)
- [SessionAuth 上下文](../../auth/context.md)
