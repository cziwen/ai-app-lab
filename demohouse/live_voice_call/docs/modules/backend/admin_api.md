# backend/admin_api.py

## 模块职责
- 提供管理后台 HTTP API：登录鉴权、岗位管理、面试管理、音频下载、公开访问校验。

## 入口与调用方
- 由 `handler.py` 调用 `create_admin_app()` 挂载并启动。
- 前端管理页面通过 `/api/admin/*` 调用。

## 对外接口（核心路由）
- 健康检查：`GET /api/health`
- 登录态：`/api/admin/auth/login`、`logout`、`me`
- 岗位：`GET/POST/DELETE /api/admin/jobs`
- 面试：`GET/POST/DELETE /api/admin/interviews`
- 音频：`GET /api/admin/interviews/{token}/audio/{track}`
- 公开访问：`GET /api/public/interviews/{token}/access`

## 关键依赖与配置
- Cookie：`ADMIN_SESSION_COOKIE`。
- CORS：`ADMIN_CORS_ORIGINS`。
- 面试链接基址：`PUBLIC_INTERVIEW_BASE_URL`。

## 日志与排障
- 重点错误类型：401（未登录）、404（资源不存在）、400（CSV/参数非法）。
- CSV 上传链路问题优先看 `parse_question_csv` 的编码与空行处理。

## 常见故障与排查步骤
1. 现象：登录后立刻掉线。
- 检查 cookie 是否被浏览器拦截（SameSite/域名/端口）。
- 检查 session 是否过期或被清理。

2. 现象：岗位导入失败。
- 确认 CSV 第一行是表头，且至少有一行有效问题。
- 编码建议 UTF-8；GBK 也支持。

3. 现象：面试详情拿不到 turns/audio。
- 仅 `completed` 面试返回完整 turns/audio 字段；未完成只返回提示信息。

## 相关测试
- `backend/tests/test_admin_api_csv.py`
