# HTTPS 部署（简化版）

域名：`smartinterview.cn`  
目标：一个脚本完成初始化、手工续订、证书激活与历史定时任务清理。

## 前置条件

- DNS A 记录已指向 ECS 公网 IP
- ECS 安全组放通 `80/443`
- 在仓库根目录执行：

```bash
cd /path/to/demohouse/live_voice_call
```

## 第一步：配置环境变量

```bash
cp .env.example .env
```

至少确认：

- `PUBLIC_INTERVIEW_BASE_URL=https://smartinterview.cn/check-in`
- `LETSENCRYPT_EMAIL=2377963631@qq.com`（建议填写你常用且可收邮件的邮箱，用于到期提醒）
- 业务凭据 `ARK_API_KEY`、`LLM1_ENDPOINT_ID`、`LLM2_ENDPOINT_ID`、`ASR_*`、`TTS_*`

## 第二步：一键初始化 HTTPS

```bash
./deploy/ssl.sh init
```

这一步会自动完成：

- 低内存模式串行构建并启动 `backend -> gateway`（适配 2C2G）
- 自动确保系统开启 swap（默认 `/swapfile`，大小 `2G`）
- 前端构建默认注入 `NODE_OPTIONS=--max-old-space-size=640`，避免 OOM
- 优先复用已有 Let’s Encrypt 证书（不存在时才申请）
- 自动切换活动证书软链 `deploy/letsencrypt/live/__active__`
- 校验并重载 Nginx

可选参数（环境变量）：

- `SWAPFILE_SIZE_GB=2`：swap 大小（单位 GB）
- `FRONTEND_NODE_OPTIONS=--max-old-space-size=640`：前端构建 Node 堆上限

若你刚升级了后端依赖（例如 `volcengine-python-sdk`），必须重建 backend 镜像：

```bash
docker compose up -d --build backend
```

## 第三步（可选）：关闭历史自动续签任务

```bash
./deploy/ssl.sh uninstall-cron
```

说明：该命令会删除旧版脚本写入的 `live-voice-ssl-renew` crontab 任务，幂等执行。

## 线上快速修复（证书已签发但链接错了）

```bash
./deploy/ssl.sh activate
```

说明：该命令不会重新签发证书，只会重新绑定最新证书并 reload Nginx。

## 手工续订 / 演练

```bash
./deploy/ssl.sh renew
./deploy/ssl.sh renew --dry-run
```

说明：自动续签安装入口已移除，续签仅支持手工触发。

## 验证

```bash
curl -I http://smartinterview.cn
curl -I https://smartinterview.cn
openssl s_client -connect smartinterview.cn:443 -servername smartinterview.cn </dev/null 2>/dev/null | openssl x509 -noout -issuer -subject -dates
```

访问：

- `https://smartinterview.cn/`
- `https://smartinterview.cn/admin/login`

## 常见问题

1. 仍是 self-signed
- 先执行：`./deploy/ssl.sh activate`
- 不行再执行：`./deploy/ssl.sh init`
- 检查：`ls -l deploy/letsencrypt/live/__active__`

2. 续订失败
- 执行：`./deploy/ssl.sh renew --dry-run`
- 查看：`docker compose logs -f gateway`

3. 后端未启动
- 查看：`docker compose logs -f backend`
- 先修复 `.env` 中 LLM/ASR/TTS 凭据

4. 出现 `AsyncArk ... no attribute responses`
- 说明 backend 容器仍在使用旧版 SDK（镜像未重建或 lock 未生效）
- 执行：`docker compose up -d --build backend`
- 校验容器内 SDK 与能力：
```bash
docker compose exec backend python -c "import importlib.metadata as m; from volcenginesdkarkruntime import AsyncArk; c=AsyncArk(base_url='https://ark.cn-beijing.volces.com/api/v3', api_key='x'); print('sdk', m.version('volcengine-python-sdk')); print('responses', hasattr(c, 'responses'))"
```
- 预期：`sdk 5.0.19` 且 `responses True`

5. 本地能力自检（可选）
```bash
cd backend
poetry run python -c "import importlib.metadata as m; from volcenginesdkarkruntime import AsyncArk; c=AsyncArk(base_url='https://ark.cn-beijing.volces.com/api/v3', api_key='x'); print('sdk', m.version('volcengine-python-sdk')); print('responses', hasattr(c, 'responses'))"
```

4. 命中 Let’s Encrypt 频率限制（too many certificates）
- 不要重复执行签发，先执行：`./deploy/ssl.sh activate`
- 当前脚本 `./deploy/ssl.sh init` 会自动复用已有证书，只有不存在时才申请
- 如必须换 identifier（例如改成新子域名），需先完成 DNS 指向并更新 `.env` 里的 `PUBLIC_INTERVIEW_BASE_URL`
