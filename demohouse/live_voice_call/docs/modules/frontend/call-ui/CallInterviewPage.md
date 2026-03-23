# frontend/src/call-ui/CallInterviewPage.tsx

## 模块概述
CallInterviewPage 是语音面试通话的主页面组件，负责渲染整个通话 UI 界面，包括参与者卡片、实时字幕、控制栏和调试面板。该组件作为通话体验的视图层，将所有 UI 元素组织在一起，并通过 useCallController 获取状态和事件处理函数。

## 模块职责
- 渲染通话页面的完整 UI 结构
- 展示面试官和候选人两个参与者卡片
- 显示实时字幕和通话状态指示
- 提供通话控制栏（麦克风、摄像头、共享、挂断等）
- 集成调试面板用于开发调试
- 响应用户控制操作并更新 UI

## UI 组件层次结构

```
CallInterviewPage
├── header (顶部栏)
│   ├── 标题和描述
│   └── header-meta (元数据)
│       ├── status-pill (连接状态)
│       └── timer-pill (通话时长)
├── end-notice (结束提示条，条件渲染)
├── call-grid (参与者网格)
│   ├── CallParticipantCard (面试官)
│   └── CallParticipantCard (候选人)
├── LiveSubtitleBar (实时字幕)
├── CallControlBar (控制栏)
└── DebugDrawer (调试抽屉，仅开发模式)
```

## 核心组件说明

### 1. Header 区域
- **标题**: "焕贞医疗 面试通话"
- **状态指示器**: 显示连接状态（已连接/未连接）
- **计时器**: 显示通话时长，格式为 MM:SS

### 2. 参与者卡片（CallParticipantCard）
**面试官卡片**:
- 名称: "面试官"
- 角色: interviewer
- 颜色主题: 蓝色渐变 (#2f8fff -> #0064e0)
- 说话状态: 基于 botSpeaking 或 botAudioPlaying
- 音量指示: 显示 interviewerAudioLevel

**候选人卡片**:
- 名称: "候选人"
- 角色: user
- 颜色主题: 青色渐变 (#25cab8 -> #11808d)
- 静音状态: 与麦克风状态绑定
- 说话状态: 基于 userSpeaking
- 音量指示: 显示 userAudioLevel

### 3. 实时字幕栏（LiveSubtitleBar）
- 显示当前说话内容的实时字幕
- 内容来源: currentBotSentence 或 currentUserSentence
- 在 mock 模式下显示模拟脚本内容

### 4. 控制栏（CallControlBar）
- 麦克风开关
- 摄像头开关
- 屏幕共享开关
- 挂断按钮
- 调试按钮（仅在 debugAllowed 时显示）

### 5. 调试抽屉（DebugDrawer）
显示条件: `debugAllowed && debugOpen`
内容包括:
- 连接状态和模式切换
- WebSocket URL 配置
- 日志面板
- 消息面板（对话记录）
- 当前识别和合成内容

## 状态管理

### UI 状态（来自 useCallController）

**连接与通话状态**:
- `isConnected`: WebSocket 连接状态
- `isInCall`: 通话中状态
- `mode`: 'real' 或 'mock' 模式
- `elapsedSec`: 通话时长（秒）

**参与者状态**:
- `interviewerSpeaking`: 面试官是否在说话
- `candidateSpeaking`: 候选人是否在说话
- `interviewerAudioLevel`: 面试官音量等级 (0-1)
- `userAudioLevel`: 候选人音量等级 (0-1)

**设备状态**:
- `micOn`: 麦克风开启状态
- `camOn`: 摄像头开启状态
- `shareOn`: 屏幕共享开启状态

**字幕与提示**:
- `subtitle`: 实时字幕文本
- `endNotice`: 结束通知消息（如倒计时提示）

### 调试状态
- `debugAllowed`: 是否允许调试（仅 localhost + token=DEBUG）
- `debugOpen`: 调试面板是否打开
- `messagePanelOpen`: 消息面板是否打开
- `transcripts`: 对话记录列表
- `debugState`: 调试详细信息

## 数据流向

```
useCallController Hook
    ↓
获取 uiState 和 debugState
    ↓
传递给各子组件
    ↓
子组件渲染 UI
    ↓
用户交互 → onControlAction
    ↓
Controller 更新状态
    ↓
UI 重新渲染
```

## UI 交互流程

### 通话建立流程
1. 页面加载，显示"未连接"状态
2. useCallController 自动尝试连接（real 模式）
3. 连接成功，状态变为"已连接"
4. 计时器开始计时
5. 用户可以开始交互（点击麦克风等）

### 通话进行中
1. 候选人点击麦克风 → candidateSpeaking 变为 true
2. 识别完成 → subtitle 显示识别内容
3. 面试官回复 → interviewerSpeaking 变为 true
4. TTS 播放 → subtitle 显示合成内容
5. 音量等级实时更新，参与者卡片显示动画

### 通话结束流程
1. 用户点击挂断或连接断开
2. 显示 endNotice（如 "15 秒后自动跳转"）
3. 倒计时归零
4. 自动跳转到结果页 `/hangup-result`

## 使用示例

```tsx
import { CallInterviewPage } from '@/call-ui/CallInterviewPage';

// 通过路由访问
// 路由配置中：<Route path="/" element={<CallInterviewPage />} />

// 页面自动使用 useCallController 管理所有状态
// 无需外部传入任何 props
```

## 入口与调用方
- **路由入口**: `frontend/src/routes/*`
- **访问路径**: `/` (根路径，需要 token 参数)
- **前置条件**:
  - 用户已通过 check-in 页面
  - SessionAuth 上下文已初始化
  - AudioChatProvider 和 AudioChatServiceProvider 已包裹

## 依赖关系

### 组件依赖
```typescript
import { CallControlBar } from '@/call-ui/components/CallControlBar';
import { CallParticipantCard } from '@/call-ui/components/CallParticipantCard';
import { DebugDrawer } from '@/call-ui/components/DebugDrawer';
import { LiveSubtitleBar } from '@/call-ui/components/LiveSubtitleBar';
```

### Hook 依赖
```typescript
import { formatDuration, useCallController } from '@/call-ui/hooks/useCallController';
```

### 间接依赖（通过 useCallController）
- AudioChatProvider: 全局状态管理
- AudioChatServiceProvider: 服务层管理
- useSessionAuth: 会话认证和媒体流管理

## 样式类名结构

```css
.call-page              /* 主容器 */
  .call-header          /* 顶部栏 */
    .header-meta        /* 元数据容器 */
      .status-pill      /* 状态徽章 */
        .is-online      /* 在线状态 */
        .is-offline     /* 离线状态 */
      .timer-pill       /* 计时器 */
  .end-notice           /* 结束提示 */
  .call-grid            /* 参与者网格 */
```

## 日志与排障

### 日志来源
- 浏览器控制台（开发环境）
- 前端日志上报接口 `/api/frontend-logs`
- DebugDrawer 中的实时日志面板

### 关键观察点
1. **连接状态**: 检查 status-pill 是否显示"已连接"
2. **计时器**: 确认通话时长正在递增
3. **参与者状态**: 说话时卡片应有视觉反馈
4. **字幕更新**: subtitle 应实时显示内容
5. **音量指示**: audioLevel 应随声音波动

## 常见故障与排查步骤

### 1. 页面空白或组件不显示
**现象**: 页面白屏或部分组件缺失

**排查步骤**:
- 检查浏览器控制台是否有 React 错误
- 确认 Provider 层级是否正确包裹
- 检查路由配置是否正确
- 验证 token 参数是否有效

### 2. 连接状态一直显示"未连接"
**现象**: status-pill 始终显示"未连接"

**排查步骤**:
- 检查 WebSocket URL 配置（通过 DebugDrawer）
- 验证后端服务是否运行
- 检查网络连接和防火墙设置
- 查看 wsConnected 状态变化日志

### 3. 字幕不更新
**现象**: 说话时 LiveSubtitleBar 无内容显示

**排查步骤**:
- 确认 currentBotSentence 和 currentUserSentence 是否有值
- 检查 WebSocket 消息是否正常接收
- 验证事件处理函数是否正确执行
- 查看 DebugDrawer 中的消息面板

### 4. 参与者卡片无说话指示
**现象**: 说话时卡片没有视觉反馈

**排查步骤**:
- 检查 speaking 状态是否正确传递
- 验证 audioLevel 数值是否更新
- 确认 CSS 动画是否正常加载
- 检查音频分析器是否正常工作

### 5. 控制栏按钮无响应
**现象**: 点击麦克风等按钮无效果

**排查步骤**:
- 确认 onControlAction 回调是否正确绑定
- 检查浏览器权限是否已授予
- 验证设备是否可用
- 查看控制台错误信息

### 6. 调试面板不显示
**现象**: 点击调试按钮无反应

**排查条件**:
- 必须在 localhost 环境
- URL 必须包含 `?token=DEBUG`
- 示例: `http://localhost:8080/?token=DEBUG`

### 7. 挂断后资源未释放
**现象**: 摄像头/麦克风指示灯仍亮

**排查步骤**:
- 检查 cleanupCaptureResources 是否执行
- 验证 mediaStreamsRef 中的 track 是否 stop
- 确认 finishImmediately 或 startAutoFinishFlow 是否被调用

## 性能优化建议

1. **避免不必要的重渲染**
   - useCallController 内部已使用 useMemo 优化
   - 子组件接收的 props 尽量保持引用稳定

2. **音量指示器优化**
   - 使用 requestAnimationFrame 更新
   - 避免在 render 中进行重计算

3. **字幕更新优化**
   - 使用防抖减少频繁更新
   - 考虑使用 CSS 过渡效果提升视觉体验

## 相关测试/验证

### 功能验证清单
- [ ] 页面加载后显示正确的初始状态
- [ ] 连接成功后状态指示器变为"已连接"
- [ ] 计时器正常计时
- [ ] 麦克风开关正常工作
- [ ] 实时字幕正确显示
- [ ] 参与者卡片说话指示正常
- [ ] 音量等级正确反映音频强度
- [ ] 挂断后正确跳转到结果页
- [ ] 调试面板（开发模式）正常显示
- [ ] 自动结束倒计时功能正常

### 端到端测试流程
1. 完成 check-in 流程
2. 进入通话页面
3. 验证连接状态和计时器
4. 测试麦克风录音和实时字幕
5. 测试面试官回复和 TTS 播放
6. 测试挂断和页面跳转
7. 验证资源正确释放

## 扩展与定制

### 自定义参与者信息
可以在 useCallController 中修改 uiState.interviewer 和 uiState.user 的配置：
```typescript
interviewer: {
  id: 'interviewer',
  name: '自定义面试官名称',
  role: 'interviewer',
  color: '自定义渐变色',
  muted: false,
}
```

### 添加新的控制按钮
在 CallControlBar 组件中扩展，并在 onControlAction 中处理新动作。

### 自定义字幕样式
修改 LiveSubtitleBar 组件的样式或实现自定义字幕渲染逻辑。

## 相关文档
- [useCallController Hook 详细文档](./hooks/useCallController.md)
- [CallControlBar 组件文档](./components/CallControlBar.md)
- [CallParticipantCard 组件文档](./components/CallParticipantCard.md)
- [LiveSubtitleBar 组件文档](./components/LiveSubtitleBar.md)
- [DebugDrawer 组件文档](./components/DebugDrawer.md)
- [AudioChatProvider 全局状态文档](../components/AudioChatProvider/index.md)
