# frontend/src/components/AudioChatProvider/index.tsx

## 模块概述
AudioChatProvider 是全局音频聊天状态管理的顶层 Provider 组件，通过 React Context 提供音频通话相关的核心状态（连接状态、说话状态、音频等级、消息列表等）。它采用简单的 useState 管理状态，作为应用级共享状态的中心，供所有子组件通过 useAudioChatState 等 Hook 访问和修改。

## 模块职责
- 提供全局音频聊天状态的 Context Provider
- 管理 WebSocket 连接状态
- 管理语音合成和播放状态
- 管理用户说话状态和音频等级
- 存储聊天消息列表
- 管理音频上下文状态（AudioContext unlock 状态）
- 管理音频路由模式（media-element 或 web-audio-fallback）

## 入口与调用方
- 在应用根组件中包裹整个应用树
- 路由主入口：`frontend/src/routes/*`
- 所有需要访问音频聊天状态的组件都依赖此 Provider

## 全局状态架构

### 状态树结构
```typescript
{
  // WebSocket 连接状态
  wsConnected: boolean,              // 是否已连接
  setWsConnected: (v: boolean) => void,

  // Bot 语音状态
  botSpeaking: boolean,              // Bot 是否正在生成语音
  setBotSpeaking: (v: boolean) => void,
  botAudioPlaying: boolean,          // Bot 音频是否正在播放
  setBotAudioPlaying: (v: boolean) => void,
  botAudioLevel: number,             // Bot 音量等级 (0-1)
  setBotAudioLevel: (v: number) => void,

  // 音频系统状态
  audioUnlocked: boolean,            // AudioContext 是否已解锁
  setAudioUnlocked: (v: boolean) => void,
  audioRouteMode: AudioRouteMode,    // 音频路由模式
  setAudioRouteMode: (v: AudioRouteMode) => void,

  // 用户语音状态
  userSpeaking: boolean,             // 用户是否正在说话
  setUserSpeaking: (v: boolean) => void,
  userAudioLevel: number,            // 用户音量等级 (0-1)
  setUserAudioLevel: (v: number) => void,

  // 消息列表
  chatMessages: IMessage[],          // 聊天消息数组
  setChatMessages: (v: IMessage[]) => void,
}
```

### AudioRouteMode 类型
```typescript
type AudioRouteMode = 'media-element' | 'web-audio-fallback';
```
- **media-element**: 使用 HTML5 Audio 元素播放（Safari Mobile 优先）
- **web-audio-fallback**: 使用 Web Audio API 播放（默认模式）

## 对外接口（导出项）

### AudioChatProvider 组件
```typescript
export const AudioChatProvider: FC<PropsWithChildren> = ({ children })
```

**Props**:
- `children`: React 子组件

**用法**:
```tsx
import { AudioChatProvider } from '@/components/AudioChatProvider';

function App() {
  return (
    <AudioChatProvider>
      {/* 应用组件树 */}
    </AudioChatProvider>
  );
}
```

## 状态管理详解

### 连接状态管理
```typescript
const [wsConnected, setWsConnected] = useState(false);
```
- 表示 WebSocket 是否已连接
- 由 VoiceBotService 的连接事件更新
- 影响 UI 连接指示器显示
- 用于控制是否允许开始录音

### Bot 语音状态
```typescript
const [botSpeaking, setBotSpeaking] = useState(false);
const [botAudioPlaying, setBotAudioPlaying] = useState(false);
const [botAudioLevel, setBotAudioLevel] = useState(0);
```

**botSpeaking**:
- 表示后端正在生成语音（TTS 合成中）
- 由 TTSSentenceStart/TTSSentenceEnd 事件控制
- 影响 UI 显示（面试官卡片说话状态）

**botAudioPlaying**:
- 表示音频正在播放
- 由 VoiceBotService 的 onStartPlayAudio/onStopPlayAudio 控制
- 用于判断是否应等待音频播放完成再结束

**botAudioLevel**:
- 音量等级，范围 0-1
- 由 Web Audio API 的 AnalyserNode 实时计算
- 用于驱动参与者卡片的音量动画

### 音频系统状态
```typescript
const [audioUnlocked, setAudioUnlocked] = useState(false);
const [audioRouteMode, setAudioRouteMode] = useState<AudioRouteMode>('web-audio-fallback');
```

**audioUnlocked**:
- 表示 AudioContext 是否已通过用户交互解锁
- 浏览器自动播放策略要求：AudioContext 必须在用户操作后才能 resume
- 通常在用户首次点击麦克风时解锁

**audioRouteMode**:
- 音频播放的技术路线
- Safari Mobile 自动选择 'media-element'
- 其他浏览器默认 'web-audio-fallback'
- 如果 Web Audio API 解码失败会自动 fallback 到 media-element

### 用户语音状态
```typescript
const [userSpeaking, setUserSpeaking] = useState(false);
const [userAudioLevel, setUserAudioLevel] = useState(0);
```

**userSpeaking**:
- 表示用户是否正在说话（录音中）
- 由 useAudioRecorder 的 recStart/recStop 控制
- 影响 UI 麦克风按钮状态和候选人卡片显示

**userAudioLevel**:
- 用户音量等级，范围 0-1
- 由录音器的音频分析器实时计算
- 用于驱动候选人卡片的音量动画

### 消息列表
```typescript
const [chatMessages, setChatMessages] = useState<IMessage[]>([]);
```

**IMessage 类型**:
```typescript
interface IMessage {
  id: string;
  role: 'user' | 'bot';
  content: string;
  createdAt: number;
}
```

- 存储对话历史记录
- 由 WebSocket 事件（ASRSentenceEnd、TTSSentenceEnd）添加
- 用于显示对话记录和调试面板

## 数据流向

```
WebSocket 事件
    ↓
VoiceBotService 处理
    ↓
调用 AudioChatProvider 的 setter
    ↓
状态更新触发 re-render
    ↓
子组件通过 useAudioChatState 获取新状态
    ↓
UI 更新
```

### 典型事件流示例

**用户说话流程**:
```
用户点击麦克风
    ↓
recStart() 调用
    ↓
setUserSpeaking(true)
    ↓
录音器启动并计算音量
    ↓
setUserAudioLevel(level) 持续更新
    ↓
用户停止说话或点击停止
    ↓
recStop() 调用
    ↓
setUserSpeaking(false)
    ↓
setUserAudioLevel(0)
```

**Bot 回复流程**:
```
后端发送 TTSSentenceStart
    ↓
setBotSpeaking(true)
    ↓
setBotSentence(sentence)
    ↓
收到音频数据帧
    ↓
onStartPlayAudio()
    ↓
setBotAudioPlaying(true)
    ↓
音频播放期间持续更新 setBotAudioLevel()
    ↓
音频播放完成
    ↓
onStopPlayAudio()
    ↓
setBotAudioPlaying(false)
    ↓
setBotAudioLevel(0)
    ↓
收到 TTSSentenceEnd
    ↓
setBotSpeaking(false)
```

## 使用示例

### 在根组件中提供 Context
```tsx
// frontend/src/App.tsx
import { AudioChatProvider } from '@/components/AudioChatProvider';
import { AudioChatServiceProvider } from '@/components/AudioChatServiceProvider';

export function App() {
  return (
    <AudioChatProvider>
      <AudioChatServiceProvider>
        <RouterProvider />
      </AudioChatServiceProvider>
    </AudioChatProvider>
  );
}
```

### 在子组件中消费状态
```tsx
import { useAudioChatState } from '@/components/AudioChatProvider/hooks/useAudioChatState';

function CallStatus() {
  const { wsConnected, botSpeaking, userSpeaking } = useAudioChatState();

  return (
    <div>
      <p>连接状态: {wsConnected ? '已连接' : '未连接'}</p>
      <p>Bot 说话中: {botSpeaking ? '是' : '否'}</p>
      <p>用户说话中: {userSpeaking ? '是' : '否'}</p>
    </div>
  );
}
```

### 更新消息列表
```tsx
import { useMessageList } from '@/components/AudioChatProvider/hooks/useMessageList';

function MessagePanel() {
  const { chatMessages, addMessage } = useMessageList();

  const handleNewMessage = () => {
    addMessage({
      id: Date.now().toString(),
      role: 'user',
      content: '你好',
      createdAt: Date.now(),
    });
  };

  return (
    <div>
      {chatMessages.map(msg => (
        <div key={msg.id}>{msg.content}</div>
      ))}
      <button onClick={handleNewMessage}>添加消息</button>
    </div>
  );
}
```

## 相关 Hook

### useAudioChatState
访问全局音频聊天状态的主要 Hook。

```typescript
import { useAudioChatState } from '@/components/AudioChatProvider/hooks/useAudioChatState';

const {
  wsConnected, setWsConnected,
  botSpeaking, setBotSpeaking,
  botAudioPlaying, setBotAudioPlaying,
  botAudioLevel, setBotAudioLevel,
  audioUnlocked, setAudioUnlocked,
  audioRouteMode, setAudioRouteMode,
  userSpeaking, setUserSpeaking,
  userAudioLevel, setUserAudioLevel,
} = useAudioChatState();
```

### useMessageList
访问和操作消息列表的 Hook。

```typescript
import { useMessageList } from '@/components/AudioChatProvider/hooks/useMessageList';

const { chatMessages, setChatMessages } = useMessageList();
```

## 依赖关系

### 依赖的类型
```typescript
import type { IMessage } from '@/types';
import type { AudioRouteMode } from '@/utils/voice_bot_service';
```

### 被依赖的组件
- CallInterviewPage（通过 useCallController）
- useVoiceBotService（更新 WebSocket 和音频状态）
- useAudioRecorder（更新用户说话状态）
- 所有需要显示实时状态的 UI 组件

## Context 定义

Context 在 `frontend/src/components/AudioChatProvider/context/index.ts` 中定义：

```typescript
import { createContext } from 'react';

export interface AudioChatContextValue {
  wsConnected: boolean;
  setWsConnected: (v: boolean) => void;
  botSpeaking: boolean;
  setBotSpeaking: (v: boolean) => void;
  botAudioPlaying: boolean;
  setBotAudioPlaying: (v: boolean) => void;
  botAudioLevel: number;
  setBotAudioLevel: (v: number) => void;
  audioUnlocked: boolean;
  setAudioUnlocked: (v: boolean) => void;
  audioRouteMode: AudioRouteMode;
  setAudioRouteMode: (v: AudioRouteMode) => void;
  userSpeaking: boolean;
  setUserSpeaking: (v: boolean) => void;
  userAudioLevel: number;
  setUserAudioLevel: (v: number) => void;
  chatMessages: IMessage[];
  setChatMessages: (v: IMessage[]) => void;
}

export const AudioChatContext = createContext<AudioChatContextValue | undefined>(undefined);
```

## 日志与排障

### 日志来源
- 状态变化不会自动记录日志
- 建议在关键 setter 调用处添加日志
- 可通过 React DevTools 观察 Context 值变化

### 关键观察点
1. **wsConnected**: 确认连接状态是否正确切换
2. **botSpeaking vs botAudioPlaying**: 理解两者区别（合成 vs 播放）
3. **audioUnlocked**: 确认 AudioContext 是否已解锁
4. **audioRouteMode**: 了解当前使用的音频播放路线
5. **chatMessages**: 验证消息是否正确添加

## 常见故障与排查步骤

### 1. Context 值为 undefined
**现象**: 使用 useAudioChatState 时报错 "Cannot read property of undefined"

**排查步骤**:
- 确认组件树被 AudioChatProvider 包裹
- 检查 Provider 的层级顺序
- 验证 useContext 调用是否在组件内部

### 2. 状态更新不触发 re-render
**现象**: 调用 setter 后组件不更新

**排查步骤**:
- 确认使用的是正确的 setter 函数
- 检查是否误用了旧的闭包值
- 验证组件是否正确订阅了 Context

### 3. 音频播放异常
**现象**: botAudioPlaying 为 true 但听不到声音

**排查步骤**:
- 检查 audioUnlocked 是否为 true
- 验证 audioRouteMode 选择是否合适
- 检查 botAudioLevel 是否有波动
- 确认 VoiceBotService 的音频播放链路

### 4. 消息列表未更新
**现象**: 对话后 chatMessages 仍为空

**排查步骤**:
- 确认 WebSocket 事件处理函数是否调用了 setChatMessages
- 检查消息格式是否符合 IMessage 接口
- 验证 useMessageList Hook 是否正确实现

### 5. userSpeaking 状态不一致
**现象**: 停止录音后 userSpeaking 仍为 true

**排查步骤**:
- 检查 recStop() 是否被调用
- 验证 setUserSpeaking(false) 是否执行
- 确认是否有其他地方误设置了 userSpeaking

## 性能优化建议

### 1. 避免不必要的 re-render
- Context 值变化会导致所有消费组件 re-render
- 考虑拆分 Context 为多个细粒度 Context
- 使用 useMemo 缓存派生状态

### 2. 状态更新优化
- 批量更新相关状态以减少 re-render
- 使用 React 18 的自动批处理特性
- 避免在循环中频繁调用 setter

### 3. 消息列表优化
- chatMessages 增长时考虑虚拟滚动
- 限制消息数量（如最多保留 100 条）
- 使用不可变更新避免引用问题

## 扩展与定制

### 添加新的全局状态
1. 在 AudioChatProvider 中添加 useState
2. 更新 AudioChatContext 类型定义
3. 在 Provider value 中传递新状态
4. 创建或更新对应的 Hook

示例：
```typescript
// 添加新状态：通话质量
const [callQuality, setCallQuality] = useState<'good' | 'poor'>('good');

// 在 Context 中提供
<AudioChatContext.Provider
  value={{
    // ...existing values
    callQuality,
    setCallQuality,
  }}
>
```

### 拆分为多个 Context
如果状态过多导致性能问题，可以拆分：

```typescript
// ConnectionContext: 仅连接状态
// AudioContext: 仅音频相关状态
// MessageContext: 仅消息列表

function App() {
  return (
    <ConnectionProvider>
      <AudioProvider>
        <MessageProvider>
          <RouterProvider />
        </MessageProvider>
      </AudioProvider>
    </ConnectionProvider>
  );
}
```

## 相关文档
- [AudioChatContext 定义](./context/index.md)
- [useAudioChatState Hook](./hooks/useAudioChatState.md)
- [useMessageList Hook](./hooks/useMessageList.md)
- [AudioChatServiceProvider 服务层](../AudioChatServiceProvider/index.md)
- [VoiceBotService 音频服务](../../utils/voice_bot_service.md)
