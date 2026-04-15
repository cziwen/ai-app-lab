# 系统整体架构

## 架构概览
本项目是实时语音面试系统，核心链路为：
- 前端 WebSocket 发送音频帧
- 后端 ASR 识别候选人回答
- LLM1（Judge）决策 `next_action`
- LLM2（Interviewer）生成面试官问句
- TTS 回传机器人语音
- 结束后可选 STT（二次录音识别）复写 candidate turns，再进入离线评分

## 核心组件
- `frontend`：候选人通话页与管理后台。
- `backend/handler.py`：三服务入口（8888/8889/8890）。
- `backend/service.py`：语音面试编排。
- `backend/interview_flow.py`：9 状态面试状态机。
- `backend/interview_judge.py`：LLM1 决策器。
- `backend/admin_store.py`：SQLite 数据读写。
- `redis`：占用控制与高并发保护辅助能力。

## 并发与准入
当前采用 `InterviewOccupancy` 快速准入模型：
- 准入成功：继续面试。
- token 重复占用：返回 `TOKEN_ALREADY_WAITING`。
- 并发满载：返回 `INTERVIEW_CAPACITY_FULL`。
- 服务异常：返回 `SERVICE_UNAVAILABLE`。

说明：当前架构无排队等待链路，不发送 `Queue*` 事件。

## 运行时端口
- `ws://<host>:8888`：面试主链路。
- `http://<host>:8889/api/frontend-logs`：前端日志上报。
- `http://<host>:8890/api/*`：管理后台 API。

## 关键错误码
- `INVALID_TOKEN`
- `TOKEN_ALREADY_WAITING`
- `INTERVIEW_CAPACITY_FULL`
- `SERVICE_UNAVAILABLE`

## 相关文档
- `docs/DATA_FLOW.md`
- `docs/TROUBLESHOOTING.md`
- `docs/modules/backend/handler.md`
- `docs/modules/backend/interview_flow.md`
- `docs/modules/backend/interview_judge.md`
