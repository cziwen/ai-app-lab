# backend/startup_self_check.py

## 模块职责
- 后端启动前统一依赖自检入口。
- 汇总并输出 `SelfCheckReport`，供 `handler.py` 决定是否启动服务。

## 当前检查项（与实现一致）
- 必检：`LLM1`、`LLM2`、`ASR`、`TTS`、`Redis`
- 可选：
  - `LLM3`：仅在配置 `LLM3_ENDPOINT_ID` 时检查
  - `STT`：仅在出现任意 `STT_*` 配置时检查；未配置时返回 `STT skipped (not configured)`

## STT 检查语义
- 若启用 STT 检查，必填项：
  - `STT_APP_ID`
  - `STT_ACCESS_TOKEN`
  - `STT_RESOURCE_ID`
  - `STT_AUDIO_PUBLIC_BASE_URL`
  - `STT_AUDIO_SIGNING_SECRET`
- `STT_SELF_CHECK_AUDIO_URL` 未配置：仅做配置完整性校验（不发起远程探测）。
- `STT_SELF_CHECK_AUDIO_URL` 已配置：执行 submit/query 探测一次可用性。

## 对外接口
- `load_runtime_config()`：加载环境变量到 `RuntimeConfig`
- `run_startup_self_check()`：执行全量检查并返回报告
- `format_self_check_lines()`：格式化日志输出行

## 相关测试
- `backend/tests/test_startup_self_check.py`
- `backend/tests/test_handler_startup.py`
