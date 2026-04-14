# frontend/src/components/AudioChatServiceProvider/index.tsx

## 模块概述
AudioChatServiceProvider 是音频聊天服务层的 Context Provider，负责管理底层音频服务相关的状态和引用，包括 WebSocket URL 配置、VoiceBotService 实例、录音器实例、音频缓冲区、当前发音人配置、实时句子内容和日志内容等。它与 AudioChatProvider 配合使用，前者管理 UI 状态，本模块管理服务层状态和实例引用。

## 模块职责
- 提供音频服务层的 Context Provider
- 管理 VoiceBotService 实例引用
- 管理录音器实例和音频缓冲区
- 存储 WebSocket URL 配置
- 管理当前发音人（TTS voice）配置
- 存储实时识别和合成的句子内容
- 收集和管理前端日志
- 提供音频采样率转换所需的中间状态

## 入口与调用方
- 在 AudioChatProvider 内部包裹应用树
- 典型层级: App -> AudioChatProvider -> AudioChatServiceProvider -> Routes
- 所有使用音频服务的 Hook 都依赖此 Provider

## 对外接口（导出项）

### AudioChatServiceProvider 组件
```typescript
export const AudioChatServiceProvider: FC<PropsWithChildren> = ({ children })
```

**Props**:
- `children`: React 子组件

**用法**:
```tsx
import { AudioChatProvider } from '@/components/AudioChatProvider';
import { AudioChatServiceProvider } from '@/components/AudioChatServiceProvider';

function App() {
  return (
    <AudioChatProvider>
      <AudioChatServiceProvider>
        <RouterProvider />
      </AudioChatServiceProvider>
    </AudioChatProvider>
  );
}
```

## 服务层状态管理

### WebSocket URL 配置
```typescript
const [wsUrl, setWsUrl] = useState(WS_URL);
```
- 默认值来自 `@/config/endpoints`
- 支持动态修改（通过调试面板）
- 连接时使用此 URL 创建 WebSocket

### 录音器和音频缓冲区引用
```typescript
const recorderRef = useRef<any>(null);
const sendPcmBufferRef = useRef(new Int16Array(0));
const sendChunkRef = useRef(null);
const sendLastFrameRef = useRef<Int16Array | null>(null);
```

**recorderRef**:
- 存储录音器实例（Recorder.js 或类似库）
- 由 useAudioRecorder Hook 设置和使用

**sendPcmBufferRef**:
- PCM 音频数据缓冲区
- 累积录音数据待发送

**sendChunkRef**:
- SampleData 采样率转换的中间结果
- 用于连续转换采样率时保持状态

**sendLastFrameRef**:
- 上次转换的最后一帧数据
- 用于确保采样率转换的连续性

### VoiceBotService 实例引用
```typescript
const serviceRef = useRef<VoiceBotService>(null);
const configNeedUpdateRef = useSyncRef(false);
const wsReadyRef = useRef(false);
```

**serviceRef**:
- 存储 VoiceBotService 实例
- 负责 WebSocket 通信和音频播放
- 生命周期由 useVoiceBotService Hook 管理

**configNeedUpdateRef**:
- 标记是否需要更新配置到后端
- 使用 useSyncRef 确保在异步回调中获取最新值

**wsReadyRef**:
- 标记 WebSocket 是否已就绪
- 用于控制是否可以发送配置更新

### 发音人配置
```typescript
const [currentSpeaker, setCurrentSpeaker] = useState('zh_female_shuangkuaisisi_moon_bigtts');
```
- 存储当前使用的 TTS 发音人 ID
- 默认值: 中文女声（双快四思）
- 可通过配置界面修改

### 实时句子内容
```typescript
const [currentUserSentence, setCurrentUserSentence] = useState('');
const [currentBotSentence, setCurrentBotSentence] = useState('');
```

**currentUserSentence**:
- 当前正在识别或刚识别完成的用户语句
- 由 ASR 事件更新
- 用于实时字幕显示

**currentBotSentence**:
- 当前正在合成或播放的 Bot 语句
- 由 TTS 事件更新
- 用于实时字幕显示

### 日志内容
```typescript
const [logContent, setLogContent] = useState<string[]>([]);
```
- 存储前端运行时日志
- 用于调试面板显示
- 可通过 useLogContent Hook 访问

## 音频技术栈说明

### 录音技术栈
1. **getUserMedia API**: 获取麦克风权限和音频流
2. **MediaRecorder / Recorder.js**: 录制音频数据
3. **Web Audio API**: 分析音频音量等级
4. **PCM 编码**: 将音频转换为 PCM 格式
5. **采样率转换**: 根据后端要求转换采样率（通常 16kHz）
6. **WebSocket**: 发送音频数据到后端

### 播放技术栈
1. **WebSocket**: 接收后端音频数据（MP3 格式）
2. **双路由策略**:
   - **Web Audio API** (默认): 使用 AudioContext 解码和播放
   - **HTML5 Audio** (fallback): Safari Mobile 或 Web Audio 失败时使用
3. **AnalyserNode**: 实时分析音量等级
4. **requestAnimationFrame**: 驱动音量可视化

## Context 定义

Context 在 `frontend/src/components/AudioChatServiceProvider/context/index.ts` 中定义：

```typescript
export interface AudioChatServiceContextValue {
  wsUrl: string;
  setWsUrl: (url: string) => void;
  recorderRef: React.MutableRefObject<any>;
  sendPcmBufferRef: React.MutableRefObject<Int16Array>;
  sendChunkRef: React.MutableRefObject<any>;
  sendLastFrameRef: React.MutableRefObject<Int16Array | null>;
  serviceRef: React.MutableRefObject<VoiceBotService | null>;
  configNeedUpdateRef: React.MutableRefObject<boolean>;
  wsReadyRef: React.MutableRefObject<boolean>;
  currentSpeaker: string;
  setCurrentSpeaker: (speaker: string) => void;
  currentUserSentence: string;
  setCurrentUserSentence: (sentence: string) => void;
  currentBotSentence: string;
  setCurrentBotSentence: (sentence: string) => void;
  logContent: string[];
  setLogContent: (logs: string[]) => void;
}
```

## 相关 Hook

### useWsUrl
管理 WebSocket URL 配置。
```typescript
import { useWsUrl } from '@/components/AudioChatServiceProvider/hooks/useWsUrl';

const { wsUrl, setWsUrl } = useWsUrl();
```

### useVoiceBotService
管理 VoiceBotService 实例和连接。
```typescript
import { useVoiceBotService } from '@/components/AudioChatServiceProvider/hooks/useVoiceBotService';

const {
  handleConnect,
  disconnectSession,
  shutdownSession,
  notifyClientHangup,
  notifyClientEndAnswer,
  isRecovering,
  reconnectExhausted,
} = useVoiceBotService();
```

### useAudioRecorder
管理录音器和音频数据发送。
```typescript
import { useAudioRecorder } from '@/components/AudioChatServiceProvider/hooks/useAudioRecorder';

const { recStart, recStop } = useAudioRecorder();
```

### useCurrentSentence
访问实时句子内容。
```typescript
import { useCurrentSentence } from '@/components/AudioChatServiceProvider/hooks/useCurrentSentence';

const { currentUserSentence, currentBotSentence } = useCurrentSentence();
```

### useLogContent
访问日志内容。
```typescript
import { useLogContent } from '@/components/AudioChatServiceProvider/hooks/useLogContent';

const { logContent, addLog } = useLogContent();
```

### useSpeakerConfig
管理发音人配置。
```typescript
import { useSpeakerConfig } from '@/components/AudioChatServiceProvider/hooks/useSpeakerConfig';

const { currentSpeaker, setCurrentSpeaker } = useSpeakerConfig();
```

## 数据流向

### 音频录制流程
```
用户点击麦克风
    ↓
recStart() 调用
    ↓
创建 Recorder 实例并存入 recorderRef
    ↓
启动录音，数据写入 sendPcmBufferRef
    ↓
定时发送音频数据到 WebSocket
    ↓
清空 sendPcmBufferRef
```

### 音频播放流程
```
WebSocket 收到音频帧
    ↓
serviceRef.current 处理
    ↓
音频数据传递给播放器
    ↓
Web Audio API 或 Audio Element 播放
    ↓
AnalyserNode 计算音量
    ↓
更新 botAudioLevel 状态
```

### 句子内容更新流程
```
WebSocket 收到 ASRSentenceEnd/TTSSentenceStart
    ↓
事件处理函数调用
    ↓
setCurrentUserSentence / setCurrentBotSentence
    ↓
useCurrentSentence Hook 返回新值
    ↓
useCallController 获取并传递给 UI
    ↓
LiveSubtitleBar 显示字幕
```

## 使用示例

### 完整集成示例
```tsx
// App.tsx
import { AudioChatProvider } from '@/components/AudioChatProvider';
import { AudioChatServiceProvider } from '@/components/AudioChatServiceProvider';
import { SessionAuthProvider } from '@/auth/context';

export function App() {
  return (
    <SessionAuthProvider>
      <AudioChatProvider>
        <AudioChatServiceProvider>
          <Router>
            <Routes>
              <Route path="/check-in" element={<CheckInPage />} />
              <Route path="/" element={<CallInterviewPage />} />
              <Route path="/hangup-result" element={<HangupResultPage />} />
            </Routes>
          </Router>
        </AudioChatServiceProvider>
      </AudioChatProvider>
    </SessionAuthProvider>
  );
}
```

### 在服务 Hook 中使用
```tsx
// useVoiceBotService.ts
import { useContext } from 'react';
import { AudioChatServiceContext } from '../context';
import VoiceBotService from '@/utils/voice_bot_service';

export const useVoiceBotService = () => {
  const context = useContext(AudioChatServiceContext);
  const { serviceRef, wsUrl, wsReadyRef } = context;

  const handleConnect = async () => {
    const service = new VoiceBotService({
      ws_url: wsUrl,
      handleJSONMessage: (json) => { /* 处理消息 */ },
      onStartPlayAudio: () => { /* 播放开始 */ },
      onStopPlayAudio: () => { /* 播放结束 */ },
    });

    await service.connect();
    serviceRef.current = service;
    wsReadyRef.current = true;
  };

  return { handleConnect };
};
```

## 依赖关系

### 核心依赖
```typescript
import type VoiceBotService from '@/utils/voice_bot_service';
import { useSyncRef } from '@/hooks/useSyncRef';
import { WS_URL } from '@/config/endpoints';
```

### 被依赖的组件
- useCallController（通过多个子 Hook）
- CallInterviewPage（间接依赖）
- DebugDrawer（访问日志和配置）

## 日志与排障

### 日志来源
- `logContent` 状态数组
- 可通过 DebugDrawer 实时查看
- 包括连接事件、音频事件、错误信息等

### 关键观察点
1. **serviceRef.current**: 确认服务实例是否已创建
2. **wsReadyRef.current**: 确认 WebSocket 是否就绪
3. **recorderRef.current**: 确认录音器是否已初始化
4. **currentUserSentence / currentBotSentence**: 确认句子是否正确更新
5. **wsUrl**: 确认 WebSocket URL 是否正确

## 常见故障与排查步骤

### 1. serviceRef.current 为 null
**现象**: VoiceBotService 未初始化，无法连接

**排查步骤**:
- 检查 handleConnect 是否被调用
- 验证 VoiceBotService 构造函数是否正确执行
- 确认没有异常阻止实例创建

### 2. 录音数据未发送
**现象**: 说话后后端无识别结果

**排查步骤**:
- 检查 recorderRef.current 是否存在
- 验证 sendPcmBufferRef 是否有数据
- 确认 WebSocket 连接状态
- 检查采样率转换是否正确

### 3. 字幕不更新
**现象**: currentUserSentence / currentBotSentence 未更新

**排查步骤**:
- 确认 WebSocket 事件处理函数是否调用 setter
- 检查事件数据格式是否正确
- 验证 useCurrentSentence Hook 是否正确消费

### 4. 日志未显示
**现象**: logContent 为空或不更新

**排查步骤**:
- 确认 setLogContent 是否被调用
- 检查日志格式是否正确（应为字符串数组）
- 验证 DebugDrawer 是否正确读取 logContent

### 5. WebSocket URL 修改无效
**现象**: 修改 wsUrl 后连接仍使用旧 URL

**排查步骤**:
- 确认 setWsUrl 是否被调用
- 检查 VoiceBotService 是否使用最新的 wsUrl
- 验证是否需要断开重连才能应用新 URL

## 性能优化建议

### 1. ref 使用优化
- 使用 ref 存储服务实例避免重新创建
- 使用 ref 存储音频缓冲区避免不必要的 re-render

### 2. 音频数据处理优化
- 批量发送音频数据减少网络请求
- 使用 ArrayBuffer 而非普通数组提高性能
- 及时清理不再使用的音频缓冲区

### 3. 日志管理优化
- 限制日志数量（如最多保留 500 条）
- 使用循环数组避免频繁 slice 操作

## 相关文档
- [AudioChatServiceContext 定义](./context/index.md)
- [useVoiceBotService Hook](./hooks/useVoiceBotService.md)
- [useAudioRecorder Hook](./hooks/useAudioRecorder.md)
- [useWsUrl Hook](./hooks/useWsUrl.md)
- [useCurrentSentence Hook](./hooks/useCurrentSentence.md)
- [useSpeakerConfig Hook](./hooks/useSpeakerConfig.md)
- [useLogContent Hook](./hooks/useLogContent.md)
- [VoiceBotService 类](../../utils/voice_bot_service.md)
- [AudioChatProvider 状态层](../AudioChatProvider/index.md)
