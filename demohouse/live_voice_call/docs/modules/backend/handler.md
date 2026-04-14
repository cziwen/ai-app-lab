# backend/handler.py

## 模块概述
`handler.py` 是后端主入口，启动并编排 3 个服务：
- 面试 WebSocket（默认 `8888`）
- 前端日志 HTTP（默认 `8889`）
- 管理后台 API（默认 `8890`）

## 准入控制（当前实现）
当前使用 `InterviewOccupancy`，不是排队模型：
- `admitted`：准入成功，进入面试流程。
- `duplicate_token`：同 token 且不同 `client_id` 重复占用，返回 `TOKEN_ALREADY_WAITING` 并关闭连接。
- `admitted`（重连接管）：同 token 且相同 `client_id` 可接管 owner，继续面试。
- `capacity_full`：并发已满，返回 `INTERVIEW_CAPACITY_FULL` 并关闭连接。
- 其他异常：返回 `SERVICE_UNAVAILABLE` 并关闭连接。

说明：当前实现没有 `QueueWaiter` 与 `Queue*` 事件链路。

## 连接链路（面试模式）
- 建链后会创建一次 `attempt_id`（`ws_session_id`），并将该 attempt 与 token/client_id/owner 绑定。
- 加载 runtime checkpoint，并注入 `VoiceBotService(interview_runtime_checkpoint=...)`。
- 服务运行过程中，通过 `on_interview_runtime_checkpoint` 异步刷盘到：
  - 轻量 checkpoint store（最新快照）
  - `interview_checkpoint_journal`（序列化快照流水）

## 关闭与结束态
- token 无效：`INVALID_TOKEN`。
- 服务初始化失败或状态异常：`SERVICE_UNAVAILABLE`。
- 连接关闭来源会记录到面试日志（例如 `client_ws`、`asr_upstream`）。
- 正常面试结束会主动关闭 ws：`code=4001`，`reason=interview_completed`。
- 候选人主动挂断使用 `code=4000`，`reason=client_hangup`。

## 持久化、评分与重试
- `PersistenceQueue` 异步落库对话与音频，并在完成后触发评分任务。
- `ScoringQueue` 调用 LLM3 生成场景段评分并写入 scorecard。
- 持久化重试参数：
  - `PERSISTENCE_MAX_RETRIES`（默认 `3`）
  - `PERSISTENCE_RETRY_BASE_SECONDS`（默认 `0.3`）
  - 退避策略：`base * 2^(retry-1)`。
- 网络断开时不会立即 completed，而是先 `mark_interview_disconnected(token, grace_seconds)`；
  - `grace_seconds` 由 `INTERVIEW_RECONNECT_GRACE_SECONDS` 控制（默认 `30`）。
- finalize 护栏：检测到 token 已被新 owner 接管时，记录 `event=interview.disconnect_mark_skipped reason=token_reacquired action=skip_complete`，旧会话降级 `disconnected` 并跳过 completed。
- persistence 护栏：写库前再次校验 owner；若 owner 已变化，记录 `event=interview_persist.complete_skipped reason=token_reacquired`，跳过 completed/scorecard/scoring。

## 相关测试
- `pytest backend/tests/test_handler_connection_close_source.py`
- `pytest backend/tests/test_handler_logging.py`
- `pytest backend/tests/test_handler_startup.py`
