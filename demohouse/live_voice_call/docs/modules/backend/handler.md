# backend/handler.py

## 模块职责
- 后端主入口，负责同时启动三类服务：
  - 面试 WebSocket（实时语音主链路）
  - 前端日志接收 HTTP 服务
  - 管理后台 FastAPI 服务
- 管理面试会话生命周期：排队、准入、断连、收尾、持久化。

## 入口与调用方
- 入口：`python -m handler`。
- 上游：进程启动器（本地运行 / Docker）。
- 下游：`service.py`（语音服务编排）、`admin_store.py`（状态与音频持久化）、`admin_api.py`（管理接口）。

## 对外接口（核心）
- `AdmissionController`：并发准入与排队控制（`MAX_ACTIVE_INTERVIEWS`）。
- `PersistenceQueue`：异步持久化队列，失败自动重试。
- `_extract_pcm_audio(...)`：解析客户端上传音频帧。

## 关键依赖与配置
- 端口：`WS_PORT`（默认 8888）、`LOG_PORT`（默认 8889）、`ADMIN_API_PORT`（默认 8890）。
- 并发与队列：`MAX_ACTIVE_INTERVIEWS`、`QUEUE_WAIT_TIMEOUT_SECONDS`、`PERSISTENCE_QUEUE_SIZE`。
- 日志：`ASYNC_LOG_*` 控制异步落盘行为。

## 日志与排障
- 全局日志：`backend/logs/backend.log`。
- 单场面试日志：`backend/data/storage/interview_logs/<token>/backend.log` 与 `frontend.log`。
- 优先观察关键词：`queue`、`timeout`、`disconnect`、`persist`、`bot error`。

## 常见故障与排查步骤
1. 现象：WebSocket 连不上。
- 检查 `handler` 进程是否已监听 `8888`。
- 检查启动前自检是否失败（LLM/ASR/TTS 任一失败会直接退出）。

2. 现象：用户长时间卡在排队。
- 检查 `MAX_ACTIVE_INTERVIEWS` 与当前 active 数。
- 检查是否有会话未正确 `release` 导致名额占用。

3. 现象：面试结束后后台看不到音频/对话。
- 检查 `PersistenceQueue` 是否出现重试或丢弃。
- 检查 `backend/data/storage/audio/<token>/` 与 turns 表是否写入成功。

## 相关测试
- `backend/tests/test_handler_startup.py`
- `backend/tests/test_handler_logging.py`
- `backend/tests/test_audio_persistence.py`
