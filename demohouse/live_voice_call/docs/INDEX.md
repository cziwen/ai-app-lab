# 语音实时通话项目文档索引

本索引提供项目所有文档的分类导航，帮助开发者快速查找所需内容。

## 快速导航

### 🏗️ 架构与设计
- [系统架构](./ARCHITECTURE.md) - 系统整体架构、组件设计、技术栈
- [数据流向](./DATA_FLOW.md) - 完整数据流、WebSocket 协议、音频流处理
- [性能优化](./PERFORMANCE.md) - 性能指标、Turn Trace、延迟优化、监控
- [故障排查](./TROUBLESHOOTING.md) - 常见问题、日志分析、故障诊断

### 📚 主文档
- [README](../README.md) - 项目介绍、快速开始、配置说明
- [部署文档](../deploy/DEPLOY.md) - Docker Compose 部署指南
- [甲方接口文档](./CLIENT_INTERVIEW_API.md) - 面试数据链路对接（curl + JSON 示例）

## 后端模块文档

### 核心服务
- [interview_flow.md](modules/backend/interview_flow.md) - 面试流程状态机（9 状态、强制转换、配置）
- [service.md](modules/backend/service.md) - VoiceBotService 核心服务（双 LLM 架构、事件循环、回调）
- [handler.md](modules/backend/handler.md) - WebSocket 处理器（准入控制、持久化队列、日志架构）

### 面试功能
- [interview_judge.md](modules/backend/interview_judge.md) - 面试评判器（LLM #1、决策逻辑、启发式算法）
- [prompt.md](modules/backend/prompt.md) - Prompt 模板管理

### API 服务
- [admin_api.md](modules/backend/admin_api.md) - 管理后台 API（认证、CRUD、CSV 题库）
- [admin_store.md](modules/backend/admin_store.md) - 数据存储层（SQLite ORM）

### 实时通信
- [event.md](modules/backend/event.md) - WebSocket 事件系统（12 种事件、Payload 定义）
- [sauc_asr_client.md](modules/backend/sauc_asr_client.md) - ASR 客户端（SAUC 协议）

### 基础设施
- [async_log.md](modules/backend/async_log.md) - 异步日志系统（队列化、丢弃策略）
- [llm_limiter.md](modules/backend/llm_limiter.md) - LLM 并发控制（信号量、死锁避免）
- [startup_self_check.md](modules/backend/startup_self_check.md) - 启动自检（LLM/ASR/TTS/Redis，含可选 STT）
- [utils.md](modules/backend/utils.md) - 工具函数集

### 测试与模拟
- [run_interview_simulation.md](modules/backend/run_interview_simulation.md) - 面试模拟器
- [check_llm_asr_tts.md](modules/backend/check_llm_asr_tts.md) - 依赖检查工具

## 前端模块文档

### 核心组件

#### 音频服务层
- [AudioChatProvider](modules/frontend/components/AudioChatProvider/index.md) - 全局音频状态管理
  - [context](modules/frontend/components/AudioChatProvider/context/index.md) - Context 定义
  - [useAudioChatState](modules/frontend/components/AudioChatProvider/hooks/useAudioChatState.md) - 状态 Hook
  - [useMessageList](modules/frontend/components/AudioChatProvider/hooks/useMessageList.md) - 消息列表 Hook

- [AudioChatServiceProvider](modules/frontend/components/AudioChatServiceProvider/index.md) - 音频技术服务
  - [context](modules/frontend/components/AudioChatServiceProvider/context/index.md) - Context 定义
  - [useVoiceBotService](modules/frontend/components/AudioChatServiceProvider/hooks/useVoiceBotService.md) - 服务实例 Hook
  - [useAudioRecorder](modules/frontend/components/AudioChatServiceProvider/hooks/useAudioRecorder.md) - 录音器 Hook
  - [useCurrentSentence](modules/frontend/components/AudioChatServiceProvider/hooks/useCurrentSentence.md) - 实时句子 Hook
  - [useLogContent](modules/frontend/components/AudioChatServiceProvider/hooks/useLogContent.md) - 日志内容 Hook
  - [useSpeakerConfig](modules/frontend/components/AudioChatServiceProvider/hooks/useSpeakerConfig.md) - 音色配置 Hook
  - [useWsUrl](modules/frontend/components/AudioChatServiceProvider/hooks/useWsUrl.md) - WebSocket URL Hook

#### 通话界面
- [CallInterviewPage](modules/frontend/call-ui/CallInterviewPage.md) - 通话主界面（UI 层次、交互流程）
- [useCallController](modules/frontend/call-ui/hooks/useCallController.md) - 通话控制器（自动结束、Mock 模式）
- [CallControlBar](modules/frontend/call-ui/components/CallControlBar.md) - 控制条组件
- [CallParticipantCard](modules/frontend/call-ui/components/CallParticipantCard.md) - 参与者卡片
- [LiveSubtitleBar](modules/frontend/call-ui/components/LiveSubtitleBar.md) - 实时字幕
- [TranscriptList](modules/frontend/call-ui/components/TranscriptList.md) - 对话记录列表
- [DebugDrawer](modules/frontend/call-ui/components/DebugDrawer.md) - 调试面板

#### UI 组件
- [ChatMessageList](modules/frontend/components/ChatMessageList.md) - 聊天消息列表
- [Panel](modules/frontend/components/Panel/index.md) - 面板组件
- [Tel](modules/frontend/components/Tel.md) - 电话组件

### 路由页面

#### 管理后台
- [admin](modules/frontend/routes/admin.md) - 管理后台框架
- [admin-login](modules/frontend/routes/admin-login.md) - 登录页
- [admin-interviews](modules/frontend/routes/admin-interviews.md) - 面试管理（CRUD、音频播放）
- [admin-jobs](modules/frontend/routes/admin-jobs.md) - 岗位管理（CSV 题库上传）

#### 候选人页面
- [check-in](modules/frontend/routes/check-in.md) - 候选人入口（设备检测、权限引导）
- [page](modules/frontend/routes/page.md) - 主页
- [layout](modules/frontend/routes/layout.md) - 布局组件
- [hangup-result](modules/frontend/routes/hangup-result.md) - 挂断结果页
- [invalid-link](modules/frontend/routes/invalid-link.md) - 无效链接页

### 认证系统
- [auth/context](modules/frontend/auth/context.md) - 认证上下文
- [auth/guards](modules/frontend/auth/guards.md) - 路由守卫
- [auth/types](modules/frontend/auth/types.md) - 类型定义
- [admin/use-admin-auth](modules/frontend/admin/use-admin-auth.md) - 管理员认证 Hook
- [admin/api](modules/frontend/admin/api.md) - Admin API 客户端

### 工具与服务
- [voice_bot_service](modules/frontend/utils/voice_bot_service.md) - WebSocket 语音服务（协议实现、音频路由）
- [log_sender](modules/frontend/utils/log_sender.md) - 日志发送器
- [utils](modules/frontend/utils/index.md) - 工具函数集
- [useSyncRef](modules/frontend/hooks/useSyncRef.md) - 同步引用 Hook

### 配置
- [endpoints](modules/frontend/config/endpoints.md) - API 端点配置
- [constant](modules/frontend/constant.md) - 常量定义
- [types](modules/frontend/types.md) - 全局类型定义

### 构建配置
- [App](modules/frontend/App.md) - 应用入口
- [modern.runtime](modules/frontend/modern.runtime.md) - Modern.js 运行时配置
- [modern-app-env.d](modules/frontend/modern-app-env.d.md) - 环境类型定义

## 文档更新记录

### 2024-03 批量更新
- ✅ 更新主 README.md：添加双 LLM 架构说明、深度思考配置、并发控制等
- ✅ 更新后端文档（8 个）：interview_flow、service、handler、admin_api、interview_judge、event、llm_limiter、async_log
- ✅ 更新前端文档（8 个）：CallInterviewPage、useCallController、AudioChatProvider、AudioChatServiceProvider、voice_bot_service、admin-interviews、admin-jobs、check-in
- ✅ 创建架构文档（4 个）：ARCHITECTURE、DATA_FLOW、PERFORMANCE、TROUBLESHOOTING
- ✅ 生成文档索引：INDEX.md

## 文档统计

- **总文档数**：70+ 个
- **后端文档**：15 个模块
- **前端文档**：49 个组件/页面/Hook
- **架构文档**：4 个核心文档
- **代码示例**：100+ 个
- **故障排查**：40+ 种场景

## 使用建议

1. **新手入门**：先阅读 [README](../README.md) → [系统架构](./ARCHITECTURE.md) → [数据流向](./DATA_FLOW.md)
2. **开发参考**：根据模块查找对应文档，每个文档都包含完整的 API 说明和使用示例
3. **问题排查**：优先查看 [故障排查](./TROUBLESHOOTING.md)，包含 40+ 种常见问题
4. **性能优化**：参考 [性能优化](./PERFORMANCE.md) 了解 Turn Trace 系统和优化策略

---

*本索引由自动化工具生成，最后更新时间：2024-03*
