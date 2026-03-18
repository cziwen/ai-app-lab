# ECS 单机部署指南（最小可用）

本文档用于在一台 ECS 上部署本项目，采用 Docker Compose + 单入口 Nginx（HTTP）。
目标：10-20 分钟内完成从配置到可访问。

## 1. 架构与端口

- 对外入口：`gateway`（Nginx）暴露 `80`
- 内部服务：`backend` 暴露容器内端口
  - WebSocket: `8888`（通过 `/ws` 代理）
  - 前端日志: `8889`（通过 `/api/frontend-logs` 代理）
  - Admin API: `8890`（通过 `/api/*` 代理）

## 2. 前置条件

- 已安装 Docker 与 Docker Compose
- ECS 安全组已放通入方向 `80` 端口
- 以下命令均在仓库根目录执行：

```bash
cd /path/to/demohouse/live_voice_call
```

## 3. 配置环境变量

1. 复制环境变量模板：

```bash
cp .env.example .env
```

2. 编辑 `.env` 并填写必填项：

- 凭据类
  - `ARK_API_KEY`
  - `LLM_ENDPOINT_ID`
  - `ASR_APP_ID`
  - `ASR_ACCESS_TOKEN`
  - `TTS_APP_ID`
  - `TTS_ACCESS_TOKEN`
  - `TTS_SPEAKER`
- 管理后台账号
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
- 外链基址
  - `PUBLIC_INTERVIEW_BASE_URL`，示例：`http://<ECS_PUBLIC_IP>/check-in`

说明：后端启动会执行 LLM/ASR/TTS 自检，以上凭据缺失或错误会导致后端直接退出。

## 4. 启动服务

```bash
docker compose up --build -d
docker compose ps
```

预期：`gateway` 和 `backend` 均为 `Up`。

## 5. 验证部署

1. 健康检查：

```bash
curl http://localhost/api/health
```

预期返回：

```json
{"status":"ok"}
```

2. 浏览器访问：

- 首页：`http://<ECS_PUBLIC_IP>/`
- 管理后台登录：`http://<ECS_PUBLIC_IP>/admin/login`
- 面试链接（示例）：`http://<ECS_PUBLIC_IP>/check-in?token=INT-xxxx`

## 6. 当前环境变量接口说明

- 后端监听（compose 已默认注入）
  - `WS_HOST` / `WS_PORT`
  - `LOG_HOST` / `LOG_PORT`
  - `ADMIN_API_HOST` / `ADMIN_API_PORT`
- 业务与凭据
  - `ARK_API_KEY`
  - `LLM_ENDPOINT_ID`
  - `ASR_APP_ID` / `ASR_ACCESS_TOKEN`
  - `TTS_APP_ID` / `TTS_ACCESS_TOKEN` / `TTS_SPEAKER`
- 管理后台
  - `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- 外链
  - `PUBLIC_INTERVIEW_BASE_URL`

## 7. 基础运维命令

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f gateway
docker compose logs -f backend

# 重启
docker compose restart

# 停止并移除容器（不删除挂载数据）
docker compose down

# 重新构建并启动
docker compose up --build -d
```

## 8. 最小排障

### 8.1 后端反复退出

现象：`backend` 容器不断重启或退出。

排查：

```bash
docker compose logs -f backend
```

常见原因：启动自检失败（凭据缺失/错误、外部依赖不可用）。

### 8.2 页面可打开但接口失败

现象：前端页面能打开，但请求报错或管理后台不可用。

排查顺序：

1. 确认 ECS 安全组已放通 `80`。
2. 查看网关日志：

```bash
docker compose logs -f gateway
```

3. 查看后端日志：

```bash
docker compose logs -f backend
```
