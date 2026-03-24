# backend/admin_api.py

## 模块概述

提供管理后台的 FastAPI 应用，包括认证、岗位管理、面试管理和音频下载等 RESTful API。

## 核心API端点

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
- `GET /api/admin/interviews/{token}/audio/{track}` - 下载音频（track=candidate|interviewer）

### 公开访问
- `GET /api/public/interviews/{token}/access` - 验证面试链接有效性（无需认证）

## 认证机制

### Session Cookie认证

```python
ADMIN_SESSION_COOKIE = "admin_session"  # Cookie名称
```

**登录流程**：
```
1. POST /api/admin/auth/login
   Body: {"username": "admin", "password": "xxx"}
2. 验证用户名密码
3. 创建session token（UUID）
4. 设置Cookie：admin_session=<token>; HttpOnly; SameSite=lax
5. 返回 {"ok": True}
```

**认证依赖**：
```python
def require_admin(request: Request) -> Dict[str, Any]:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    admin = get_admin_by_session(token)
    if not admin:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return admin
```

### CORS配置

```python
# 环境变量 ADMIN_CORS_ORIGINS，逗号分隔
ADMIN_CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080
```

## CSV题库格式

### 表头定义

```python
CSV_TEMPLATE_COLUMNS = [
    "问题",            # 必填
    "能力维度",        # 选填
    "评分分界线",      # 选填，用于InterviewJudge
    "最好标准",        # 选填
    "中等标准",        # 选填
    "最差标准",        # 选填
    "输出格式",        # 选填
]
```

### 解析规则

```python
def parse_question_csv(upload: UploadFile) -> List[Dict]:
    # 1. 编码检测：utf-8-sig → utf-8 → gbk
    # 2. 跳过空行和全空白行
    # 3. 列数自动补齐/截断
    # 4. 至少包含一行有效问题（问题字段非空）
```

**示例CSV**：
```csv
问题,能力维度,评分分界线,最好标准,中等标准,最差标准,输出格式
请介绍你的项目经验,项目管理,是否包含目标、动作、结果,清晰完整,基本涵盖,缺失关键要素,
```

### 错误码

| 状态码 | 错误 | 说明 |
|-------|------|------|
| 400 | CSV文件为空 | 上传的文件大小为0 |
| 400 | CSV编码无法识别 | 非UTF-8/GBK编码 |
| 400 | CSV缺少表头 | 文件没有第一行 |
| 400 | CSV表头不匹配 | 列名与模板不一致 |
| 400 | CSV题库为空 | 没有有效问题行 |

## 面试链接生成

```python
def build_interview_link(token: str) -> str:
    domain = os.getenv("INTERVIEW_BASE_DOMAIN") or "http://localhost:8080"
    return f"{domain.rstrip('/')}/check-in?token={token}"
```

**环境变量配置**：
```bash
INTERVIEW_BASE_DOMAIN=https://yourdomain.com
```

## API请求示例

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
  -F "question_bank=@questions.csv"
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

### 下载音频
```bash
curl -X GET http://localhost:8890/api/admin/interviews/<token>/audio/candidate \
  -b cookies.txt \
  -o candidate.wav
```

## 故障排查

### 1. 登录后立刻掉线

**原因**：Cookie被浏览器拦截
**解决**：
- 检查CORS配置是否包含前端域名
- 确认Cookie的SameSite/Secure属性与部署环境匹配
- 生产环境建议使用HTTPS

### 2. CSV导入失败

**原因**：编码或格式问题
**解决**：
```bash
# 转换为UTF-8编码
iconv -f gbk -t utf-8 input.csv > output.csv

# 检查表头
head -1 questions.csv
```

### 3. 音频下载404

**原因**：面试未完成或音频持久化失败
**解决**：
```bash
# 检查面试状态
curl http://localhost:8890/api/admin/interviews/<token> -b cookies.txt

# 检查音频文件
ls backend/data/storage/audio/<token>/
```

## 相关测试

```bash
pytest backend/tests/test_admin_api_auth.py
pytest backend/tests/test_admin_api_csv.py
pytest backend/tests/test_admin_api_interview.py
```
