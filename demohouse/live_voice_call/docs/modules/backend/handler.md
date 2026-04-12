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

## 连接与关闭
- token 无效：`INVALID_TOKEN`。
- 服务初始化失败或状态异常：`SERVICE_UNAVAILABLE`。
- 连接关闭来源会记录到面试日志（例如 `client_ws`、`asr_upstream`）。

## 持久化与评分
- `PersistenceQueue` 异步落库对话与音频，并在完成后触发评分任务。
- `ScoringQueue` 调用 LLM3 生成场景段评分并写入 scorecard。
- finalize 护栏：检测到 token 已被新 owner 接管时，记录 `event=interview.disconnect_mark_skipped reason=token_reacquired action=skip_complete`，旧会话降级 `disconnected` 并跳过 completed。
- persistence 护栏：写库前再次校验 owner；若 owner 已变化，记录 `event=interview_persist.complete_skipped reason=token_reacquired`，跳过 completed/scorecard/scoring。

## 相关测试
- `pytest backend/tests/test_handler_connection_close_source.py`
- `pytest backend/tests/test_handler_logging.py`
- `pytest backend/tests/test_handler_startup.py`
