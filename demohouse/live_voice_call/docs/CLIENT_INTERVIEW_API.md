# 甲方对接接口文档（面试数据链路）

本文档用于甲方技术对接，聚焦“面试数据可获取接口”。

## 1. 接口约定

- Base URL：`https://smartinterview.cn`
- 内容类型：`application/json`
- 认证方式：`admin_session` Cookie（通过登录接口获取）
- 编码：UTF-8

### 1.1 常见状态码

| 状态码 | 含义 | 常见场景 |
| --- | --- | --- |
| 200 | 请求成功 | 查询成功、下载成功 |
| 400 | 请求参数不符合业务规则 | 业务参数错误（少量接口会返回） |
| 401 | 未登录或登录过期 | 未携带有效 `admin_session` |
| 404 | 资源不存在或已失效 | token 不存在、面试链接失效、音频不存在 |

## 2. 快速调用流程（一条龙）

可直接复制执行，完成“健康检查 -> 登录 -> 拉列表 -> 查详情 -> 下载音频”。

```bash
BASE_URL="https://smartinterview.cn"
COOKIE_FILE="./cookie.txt"
TOKEN="INT-请替换为真实token"

# 1) 健康检查
curl -v -m 10 "$BASE_URL/api/health"

# 2) 登录（请替换账号密码）
curl -v -m 15 -X POST "$BASE_URL/api/admin/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"请替换为真实密码"}' \
  -c "$COOKIE_FILE"

# 3) 面试列表
curl -v -m 15 "$BASE_URL/api/admin/interviews?page=1&page_size=20&q=" \
  -b "$COOKIE_FILE"

# 4) 面试详情
curl -v -m 15 "$BASE_URL/api/admin/interviews/$TOKEN" \
  -b "$COOKIE_FILE"

# 5) 下载候选人音频
curl -L -v -m 30 "$BASE_URL/api/admin/interviews/$TOKEN/audio/candidate" \
  -b "$COOKIE_FILE" --output "${TOKEN}-candidate.wav"

# 6) 下载面试官音频
curl -L -v -m 30 "$BASE_URL/api/admin/interviews/$TOKEN/audio/interviewer" \
  -b "$COOKIE_FILE" --output "${TOKEN}-interviewer.mp3"
```

## 3. 接口明细

### 3.1 健康检查

- 方法/路径：`GET /api/health`
- 认证：无需

请求示例：

```bash
curl -v -m 10 'https://smartinterview.cn/api/health'
```

成功响应示例：

```json
{
  "status": "ok"
}
```

失败响应示例：

- 通常是网关或后端不可达（如 502/超时），无固定业务 JSON 结构。

---

### 3.2 管理员登录（获取 Cookie）

- 方法/路径：`POST /api/admin/auth/login`
- 认证：无需（登录后返回 Cookie）
- 请求体：JSON

请求示例：

```bash
curl -v -m 15 -X POST 'https://smartinterview.cn/api/admin/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"请替换为真实密码"}' \
  -c cookie.txt
```

成功响应示例：

```json
{
  "ok": true
}
```

失败响应示例（账号或密码错误）：

```json
{
  "detail": "用户名或密码错误"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| username | string | 是 | 管理员用户名 |
| password | string | 是 | 管理员密码 |

对接提示：

- 请保存 `Set-Cookie` 返回的 `admin_session`，后续管理接口都需要。
- 建议所有管理接口请求都带 `-b cookie.txt`。

---

### 3.3 面试列表查询

- 方法/路径：`GET /api/admin/interviews`
- 认证：需要 `admin_session` Cookie
- Query 参数：
  - `q`：搜索关键字（可匹配 token、候选人名、岗位名）
  - `page`：页码（>=1）
  - `page_size`：每页数量（1-100）

请求示例：

```bash
curl -v -m 15 'https://smartinterview.cn/api/admin/interviews?page=1&page_size=20&q=' \
  -b cookie.txt
```

成功响应示例：

```json
{
  "items": [
    {
      "token": "INT-abc123xyz",
      "candidate_name": "张三",
      "question_count": 3,
      "notes": "一面",
      "status": "completed",
      "interruption_count": 0,
      "created_at": "2026-03-23T09:30:00+00:00",
      "completed_at": "2026-03-23T09:45:00+00:00",
      "expires_at": "2026-03-24T09:30:00+00:00",
      "job": {
        "job_uid": "JOB-20260323-12ABCD",
        "name": "后端工程师"
      }
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

失败响应示例（未登录或会话过期）：

```json
{
  "detail": "未登录或登录已过期"
}
```

关键字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| items[].token | string | 面试唯一标识，后续详情/音频下载依赖此字段 |
| items[].status | string | 面试状态：`pending`/`in_progress`/`completed`/`failed`/`deleted` |
| items[].interruption_count | number | 中断次数 |
| items[].expires_at | string(datetime) | 面试 token 过期时间（UTC ISO8601） |
| total | number | 总记录数 |

---

### 3.4 面试详情查询

- 方法/路径：`GET /api/admin/interviews/{token}`
- 认证：需要 `admin_session` Cookie

请求示例：

```bash
curl -v -m 15 'https://smartinterview.cn/api/admin/interviews/INT-abc123xyz' \
  -b cookie.txt
```

成功响应示例（已完成面试）：

```json
{
  "interview": {
    "token": "INT-abc123xyz",
    "candidate_name": "张三",
    "question_count": 3,
    "notes": "一面",
    "status": "completed",
    "interruption_count": 0,
    "created_at": "2026-03-23T09:30:00+00:00",
    "completed_at": "2026-03-23T09:45:00+00:00",
    "job": {
      "job_uid": "JOB-20260323-12ABCD",
      "name": "后端工程师"
    },
    "selected_questions": [
      {
        "sort_order": 1,
        "question_id": 101,
        "question": "请介绍你做过的最复杂项目",
        "max_followups": 2
      }
    ],
    "required_checkins": ["speaker", "mic"],
    "interview_link": "https://smartinterview.cn/check-in?token=INT-abc123xyz",
    "completed": true,
    "turns": [
      {
        "role": "interviewer",
        "content": "你好，欢迎参加面试。",
        "created_at": "2026-03-23T09:30:10+00:00",
        "sort_order": 1
      },
      {
        "role": "candidate",
        "content": "你好，我先做一下自我介绍。",
        "created_at": "2026-03-23T09:30:20+00:00",
        "sort_order": 2
      }
    ],
    "audio": {
      "candidate_url": "/api/admin/interviews/INT-abc123xyz/audio/candidate",
      "interviewer_url": "/api/admin/interviews/INT-abc123xyz/audio/interviewer"
    }
  }
}
```

成功响应示例（未完成面试）：

```json
{
  "interview": {
    "token": "INT-not-finished",
    "candidate_name": "李四",
    "question_count": 3,
    "notes": null,
    "status": "in_progress",
    "interruption_count": 1,
    "created_at": "2026-03-23T10:00:00+00:00",
    "completed_at": null,
    "job": {
      "job_uid": "JOB-20260323-34EFGH",
      "name": "产品经理"
    },
    "selected_questions": [],
    "required_checkins": ["speaker", "mic"],
    "interview_link": "https://smartinterview.cn/check-in?token=INT-not-finished",
    "completed": false,
    "completion_message": "用户还没有完成面试"
  }
}
```

失败响应示例（token 不存在）：

```json
{
  "detail": "面试不存在"
}
```

关键字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| interview.completed | boolean | 是否已完成。`true` 时返回 `turns` 与 `audio` |
| interview.turns | array | 对话逐轮记录（仅完成态返回；candidate 侧为评分口径文本，优先 STT、失败回退 ASR） |
| interview.audio | object | 音频下载地址（仅完成态返回，且仍需 cookie） |
| interview.required_checkins | string[] | 候选人入场设备检查项（speaker/mic/camera/screen 子集） |
| interview.interview_link | string | 候选人访问链接 |

对接提示：

- `audio.candidate_url` / `audio.interviewer_url` 是相对路径，调用时要拼接域名。
- 未完成面试不会返回 `turns` 和 `audio`，请用 `completed` 字段判断。
- 完成态 `turns` 与评分输入保持同口径：candidate 文本优先使用评分前 STT 结果，STT 不可用时按轮次回退 ASR。

---

### 3.5 面试音频下载

- 方法/路径：`GET /api/admin/interviews/{token}/audio/{track}`
- 认证：需要 `admin_session` Cookie
- 路径参数：
  - `track=candidate`：候选人音频
  - `track=interviewer`：面试官音频

请求示例（候选人）：

```bash
curl -L -v -m 30 'https://smartinterview.cn/api/admin/interviews/INT-abc123xyz/audio/candidate' \
  -b cookie.txt --output candidate.wav
```

请求示例（面试官）：

```bash
curl -L -v -m 30 'https://smartinterview.cn/api/admin/interviews/INT-abc123xyz/audio/interviewer' \
  -b cookie.txt --output interviewer.mp3
```

成功响应：

- 返回二进制音频流（常见 `audio/wav` 或 `audio/mpeg`）。

失败响应示例（音频不存在或 track 非法）：

```json
{
  "detail": "音频不存在"
}
```

对接提示：

- 只有完成且已持久化音频的面试，才可下载。
- 推荐先调用详情接口，确认 `completed=true` 且有 `audio` 字段后再下载。

---

### 3.6 公开访问校验（无需登录）

- 方法/路径：`GET /api/public/interviews/{token}/access`
- 认证：无需
- 用途：候选人进入面试前校验链接是否有效

请求示例：

```bash
curl -v -m 10 'https://smartinterview.cn/api/public/interviews/INT-abc123xyz/access'
```

成功响应示例：

```json
{
  "interview": {
    "token": "INT-abc123xyz",
    "candidate_name": "张三",
    "status": "pending",
    "interruption_count": 0,
    "required_checkins": ["speaker", "mic"],
    "job": {
      "job_uid": "JOB-20260323-12ABCD",
      "name": "后端工程师"
    }
  }
}
```

失败响应示例（无效/失效）：

```json
{
  "detail": "面试链接无效或已失效"
}
```

对接提示：

- 该接口只在面试处于可进入状态时返回 200。
- 对已结束、已失效、token 不存在的链接，统一返回 404。

---

### 3.7 公开音频下载（签名鉴权，无需登录）

- 方法/路径：`GET /api/public/interviews/{token}/audio/{track}?exp=...&sig=...`
- 认证：无需登录；通过 URL 签名鉴权
- 路径参数：
  - `track=candidate`：候选人音频
  - `track=interviewer`：面试官音频
- 查询参数：
  - `exp`：过期时间（UTC 秒时间戳）
  - `sig`：签名（HMAC-SHA256）
  - `question_id`（可选）：题目 ID，仅 `track=candidate` 有效
  - `question_epoch`（可选，默认 0）：题目 epoch，仅 `track=candidate` 有效

请求示例：

```bash
curl -L -v -m 30 'https://smartinterview.cn/api/public/interviews/INT-abc123xyz/audio/candidate?exp=1760000000&sig=请替换签名' \
  --output candidate.wav
```

题级音频示例：

```bash
curl -L -v -m 30 'https://smartinterview.cn/api/public/interviews/INT-abc123xyz/audio/candidate?exp=1760000000&sig=请替换签名&question_id=q1&question_epoch=0' \
  --output q1_epoch0.wav
```

成功响应：

- 返回二进制音频流（常见 `audio/wav` 或 `audio/mpeg`）。

失败响应示例（签名无效或过期）：

```json
{
  "detail": "forbidden"
}
```

对接提示：

- 该接口主要用于 STT 服务端拉取音频（默认优先拉取题级音频）。
- `exp` 过期或 `sig` 与服务端不匹配时返回 403。
- 题级参数命中失败时返回 404（常见于 question_id/epoch 不匹配或题级音频未生成）。
- 对外系统若需长期访问，请通过业务侧重新申请短时签名 URL，不建议复用过期链接。

## 4. 对接建议

- 建议先用“快速调用流程”串通后，再做系统级对接。
- 管理接口统一通过 cookie 会话鉴权，不建议把账号密码写入代码仓库。
- 音频下载接口依赖登录态，即使拿到了详情里的 `audio` URL，也必须携带 cookie。
- 若甲方服务端转发这些接口，建议保留原始状态码与 `detail` 字段，便于排查。

## 5. 附：最小排障命令

```bash
# 1) 检查是否已登录
curl -v -m 10 'https://smartinterview.cn/api/admin/interviews?page=1&page_size=1' -b cookie.txt

# 2) 若返回 401，重新登录获取 cookie
curl -v -m 15 -X POST 'https://smartinterview.cn/api/admin/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"请替换为真实密码"}' \
  -c cookie.txt

# 3) 验证公开链接
curl -v -m 10 'https://smartinterview.cn/api/public/interviews/INT-请替换/access'
```
