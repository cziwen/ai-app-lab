# backend/admin_store.py

## 模块职责
- 管理后台与面试数据的统一存储层（SQLite + 文件系统）。
- 负责：管理员会话、岗位与题库、面试状态机、对话轮次、音频文件路径、canonical 构建与评分输入生成。

## 入口与调用方
- 由 `admin_api.py`（HTTP 管理接口）和 `handler.py`（实时面试流程）共同调用。

## 对外接口（核心）
- 管理员：`ensure_default_admin`、`verify_admin_credentials`、`create_admin_session`。
- 岗位：`create_job`、`list_jobs`、`get_job_detail`、`delete_job_cascade`。
- 面试：`create_interview`、`start_interview_session`、`resolve_interview_timeout`、`mark_interview_completed`。
- 数据落盘：`save_interview_turn_events`、`persist_interview_audio`、`get_audio_file_path`。
- 收口与评分输入：`finalize_canonical_artifacts`、`build_score_inputs_from_canonical_turns`。

## 存储与状态
- DB：`backend/data/app.db`（WAL 模式）。
- 文件：`backend/data/storage/audio`、`backend/data/storage/interview_logs`。
- 关键状态：`pending / in_progress / completed / failed / deleted`。
- 断连容忍：`interruption_count` + `reconnect_deadline_at`。

## 日志与排障
- 与本模块相关的问题通常表现为：状态不一致、后台列表异常、音频缺失。
- 排查顺序：
  1. 查 interviews 表状态与时间戳。
  2. 查 interview_turn_events / interview_turns 是否一致。
  3. 查 audio 目录是否与数据库路径一致。
  4. 查 `consistency_flags` 中是否出现 STT 相关标记。

## 常见故障与排查步骤
1. 现象：后台显示“面试进行中”但用户已离线。
- 检查 `mark_interview_disconnected` 是否被调用。
- 检查 `resolve_interview_timeout` 的触发时机。

2. 现象：创建面试失败。
- 检查目标岗位是否存在、题库是否为空。
- 检查 `duration_minutes` 和题目数量计算逻辑。

3. 现象：音频 URL 返回 404。
- 检查 `candidate_audio_path/interviewer_audio_path` 字段。
- 检查物理文件是否存在并与 token 对齐。

4. 现象：评分文本与实时 ASR 文本不一致。
- 说明：收口后会优先使用 STT 复写 candidate turns（若启用 STT）。
- 观察 `consistency_flags`：
  - `stt_applied`：已应用 STT
  - `stt_partial_fallback`：部分轮次回退 ASR
  - `stt_full_fallback`：整场回退 ASR
  - `stt_failed:*`：STT 调用失败类型

## 相关测试
- `backend/tests/test_admin_store.py`
- `backend/tests/test_audio_persistence.py`
