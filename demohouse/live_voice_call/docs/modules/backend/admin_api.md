# backend/admin_api.py

## 模块概述

提供管理后台的 FastAPI 应用，包括认证、岗位管理、面试管理和音频下载等 RESTful API。

## 核心 API 端点

### 认证相关
- `POST /api/admin/auth/login` - 管理员登录，返回 session cookie
- `POST /api/admin/auth/logout` - 退出登录
- `GET /api/admin/auth/me` - 获取当前登录管理员信息

### 岗位管理
- `GET /api/admin/jobs` - 分页查询岗位列表
- `POST /api/admin/jobs` - 创建岗位（需上传题库 CSV）
- `GET /api/admin/jobs/{job_uid}` - 查询岗位详情
- `DELETE /api/admin/jobs/{job_uid}` - 删除岗位（级联删除关联面试）

### 面试管理
- `GET /api/admin/interviews` - 分页查询面试列表
- `POST /api/admin/interviews` - 创建面试
- `GET /api/admin/interviews/{token}` - 查询面试详情
- `DELETE /api/admin/interviews/{token}` - 删除面试
- `GET /api/admin/interviews/{token}/audio/{track}` - 下载音频（`track=candidate|interviewer`）

### 公开访问
- `GET /api/public/interviews/{token}/access` - 验证面试链接有效性（无需认证）

## 认证机制

### Session Cookie 认证

```python
ADMIN_SESSION_COOKIE = "admin_session"
```

登录成功后写入 `HttpOnly` Cookie，并通过 `require_admin` 依赖保护管理端接口。

## CORS 配置

```bash
ADMIN_CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
```

## 题库 CSV v2（唯一支持格式）

### 表头（固定 4 列）

```text
场景,问题,评分标准,最大分数
```

后端 `parse_question_csv` 仅接受上述表头。旧 8 列模板会直接返回 `400 CSV 表头不匹配`。

### 行级语义（严格校验）

- `问题`：每行必填。
- `场景`：可空；空值表示延续当前场景连续段。
- `评分标准`、`最大分数`：仅场景首问必填。
- 场景子问（同段后续行）这两列必须为空；若填写直接报错（防歧义）。
- 首行若 `场景` 为空，直接报错（无法确定可延续场景）。

### 场景段规则

- 行内 `场景` 从空变为非空：开启新场景段。
- 连续空 `场景` 行：都归属当前场景段。
- 后续再次出现同名 `场景`：视为新场景段（不回连旧段）。

## 评分口径（与服务层一致）

- LLM3 按“场景连续段”评分，不按 CSV 每一行单独评分。
- 同一场景段内多个子问的回答会在评分前聚合为一份 `aggregated_answer`。
- 每个场景段只调用一次评分模型。
- 评分输出契约固定为：
  - `numeric_score`
  - `comment`（需包含得分原因/过程）

### 最大分数解析

`最大分数` 使用 `parse_score_scale` 解析，支持如：
- `5`
- `5分`
- `0-5`
- `0~5`
- `0～5分`

解析失败返回 `400` 并附带行号与格式提示。

### 最小可用示例

```csv
场景,问题,评分标准,最大分数
项目复盘场景,请先介绍项目目标与背景,关注目标/约束/结果是否完整且可追问,5
,你在这个项目里承担了什么关键职责,,
,项目最终结果如何，有哪些可量化指标,,
线上故障场景,描述一次线上故障处理（发现-定位-止血-复盘）,关注排障路径与优先级判断,5
,如果同类故障再次发生你会如何机制化避免,,
```

完整模板见：`demo_resource/question_bank_v2_template.csv`。

## 错误码与高频报错

| 状态码 | 错误示例 | 说明 |
|---|---|---|
| 400 | `CSV 文件为空` | 上传内容为空 |
| 400 | `CSV 编码无法识别，请使用 UTF-8` | 非 UTF-8/GBK 编码 |
| 400 | `CSV 缺少表头` | 文件无首行 |
| 400 | `CSV 表头不匹配。期望: 场景,问题,评分标准,最大分数` | 非 v2 表头 |
| 400 | `CSV 第N行“场景”为空，且前面没有可延续的场景` | 首段无法建立 |
| 400 | `CSV 第N行“评分标准”不能为空（场景首问必填）` | 场景首问缺评分标准 |
| 400 | `CSV 第N行“最大分数”不能为空（场景首问必填）` | 场景首问缺最大分数 |
| 400 | `CSV 第N行“评分标准”必须留空（场景子问不允许填写）` | 子问误填评分标准 |
| 400 | `CSV 第N行“最大分数”必须留空（场景子问不允许填写）` | 子问误填最大分数 |
| 400 | `CSV 第N行“最大分数”格式无效...` | 分值格式不可解析 |

## 面试链接生成

```python
def build_interview_link(token: str) -> str:
    domain = os.getenv("INTERVIEW_BASE_DOMAIN")
    return f"{domain.rstrip('/')}/check-in?token={token}"
```

## API 请求示例

### 登录

```bash
curl -X POST http://localhost:8890/api/admin/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"password"}' \
  -c cookies.txt
```

### 创建岗位

```bash
curl -X POST http://localhost:8890/api/admin/jobs \
  -b cookies.txt \
  -F "name=后端工程师" \
  -F "duties=负责后端开发" \
  -F "requirements=3年经验" \
  -F "question_bank=@demo_resource/question_bank_v2_template.csv"
```

### 创建面试

```bash
curl -X POST http://localhost:8890/api/admin/interviews \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{
    "candidate_name": "张三",
    "job_uid": "job-xxx",
    "duration_minutes": 30,
    "required_checkins": ["mic", "camera"]
  }'
```

## 排障建议

### 1. 旧模板上传失败

现象：`CSV 表头不匹配`

处理：改为 `场景,问题,评分标准,最大分数` 四列表头，并按场景首问/子问规则填写。

### 2. 子问误填评分字段

现象：`评分标准/最大分数必须留空`

处理：仅场景首问填写评分配置；子问留空。

### 3. 首行场景为空

现象：`场景为空，且前面没有可延续的场景`

处理：第一条必须是场景首问，显式填写 `场景 + 评分标准 + 最大分数`。

## 相关测试

```bash
pytest backend/tests/test_admin_api_csv.py
pytest backend/tests/test_admin_api_interview_checkins.py
pytest backend/tests/test_admin_login_guard.py
```
