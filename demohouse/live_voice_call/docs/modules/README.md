# 文档总览

## 架构与流程文档

- [ARCHITECTURE.md](../ARCHITECTURE.md) - 系统整体架构图和说明
  - 系统架构概览
  - 组件架构（前后端、双 LLM、ASR/TTS，含可选 STT 收口）
  - 技术栈说明
  - 部署架构

- [DATA_FLOW.md](../DATA_FLOW.md) - 完整数据流向图
  - 面试流程数据流
  - WebSocket 通信流程
  - 音频流处理
  - 状态转换流程

- [PERFORMANCE.md](../PERFORMANCE.md) - 性能优化和监控指南
  - 性能指标定义
  - Turn Trace 系统说明
  - 延迟优化策略
  - 并发控制策略
  - 监控与告警

- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) - 常见问题排查手册
  - 启动失败排查
  - 连接问题排查
  - 音频问题排查
  - 性能问题排查
  - 日志分析指南

## 模块文档

### 目标
- 为排障提供"按模块定位"的快速入口，降低从故障现象到源码位置的定位时间。

### 覆盖统计
- Backend 模块文档：15
- Frontend 模块文档：49

### 快速入口
- [后端模块索引](./backend/README.md)
- [前端模块索引](./frontend/README.md)

## 主链路排障建议
1. 先看前端路由与守卫（token/session 与设备检查）。
2. 再看前端实时链路（录音、WS、事件解析、字幕/消息状态）。
3. 再看后端连接处理与事件流（`handler`、`event`、`utils`）。
4. 最后看持久化和管理面逻辑（`admin_store`、`admin_api`）。

## 文档约定
- 每个文档对应一个源码模块。
- 每个文档统一包含：职责、入口、接口、依赖、日志、常见故障、验证建议。
