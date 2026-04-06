# 常见问题排查手册

## 1. 后端容器反复退出
常见原因：启动自检失败（LLM1/LLM2/ASR/TTS/Redis 任一失败）。

排查：
```bash
docker compose logs -f backend
```
重点检查环境变量：
- `ARK_API_KEY`
- `LLM1_ENDPOINT_ID`
- `LLM2_ENDPOINT_ID`
- `ASR_APP_ID` / `ASR_ACCESS_TOKEN` / `ASR_RESOURCE_ID`
- `TTS_APP_ID` / `TTS_ACCESS_TOKEN` / `TTS_SPEAKER`
- `REDIS_URL`

## 2. WebSocket 建连后立即断开
先看前端收到的 `BotError.code`：
- `INVALID_TOKEN`：链接无效或已失效。
- `TOKEN_ALREADY_WAITING`：同一面试链接已在其他页面占用。
- `INTERVIEW_CAPACITY_FULL`：并发已满，当前版本会快速失败。
- `SERVICE_UNAVAILABLE`：后端暂时不可用。

排查建议：
```bash
# 查看后端日志
docker compose logs -f backend

# 本地直跑时可看最新文件日志
latest_log=$(ls -t backend/logs/backend-*.log | head -1)
tail -f "$latest_log"
```

## 3. 识别正常但机器人无回复
- 检查是否收到 `SentenceRecognized` 后的 `TTSSentenceStart/TTSDone`。
- 检查 LLM2 配置（`LLM2_ENDPOINT_ID`、`ARK_API_KEY`）。
- 检查 `backend/tests/test_service_interview_llm.py` 对应上下文构建与 prompt 约束。

## 4. 占用无法释放 / 并发长期满载
当前准入由 `InterviewOccupancy` 管理（TTL + heartbeat）。

排查：
- 检查 Redis 可用性。
- 检查 `INTERVIEW_OCCUPANCY_TTL_SECONDS` 与 `INTERVIEW_OCCUPANCY_HEARTBEAT_SECONDS`。
- 检查会话是否异常退出导致 heartbeat 中断。

## 5. Prompt 质量回归
可运行 live QA（默认跳过）：
```bash
cd backend
RUN_LIVE_PROMPT_QA=1 \
PROMPT_QA_CSV=/abs/path/to/job.csv \
PROMPT_QA_STRICT=1 \
pytest -q -o addopts='' tests/test_prompt_quality_live_api.py
```

可选参数：
- `PROMPT_QA_LIMIT`
- `PROMPT_QA_OUTPUT`

## 6. 建议的最小回归集
```bash
cd backend
PYTHONPATH=. pytest -q -o addopts='' \
  tests/test_interview_flow.py \
  tests/test_service_interview_llm.py \
  tests/test_handler_connection_close_source.py
```
