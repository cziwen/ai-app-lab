# ECS 单机部署指南（HTTPS 正式对外）

本文档用于在一台 ECS 上部署本项目，并为 `smartinterview.cn` 配置 Let’s Encrypt 免费证书与自动续订。

## 1. 架构说明

- `gateway`（Nginx）对外暴露 `80/443`
- `backend` 仅容器网络可见：`8888/8889/8890`
- 证书方案：Let’s Encrypt + HTTP-01（`/.well-known/acme-challenge/`）

## 2. 前置检查

以下命令均在仓库根目录执行：

```bash
cd /path/to/demohouse/live_voice_call
```

确认 DNS 已解析到 ECS：

```bash
dig +short smartinterview.cn
```

要求：返回 ECS 公网 IP。

确认安全组放通：

- `80/tcp`
- `443/tcp`

## 3. 环境变量

```bash
cp .env.example .env
```

重点检查 `.env`：

- `PUBLIC_INTERVIEW_BASE_URL=https://smartinterview.cn/check-in`
- 凭据：`ARK_API_KEY`、`LLM_ENDPOINT_ID`、`ASR_*`、`TTS_*`
- 管理员：`ADMIN_USERNAME`、`ADMIN_PASSWORD`

## 4. 启动网关与后端

```bash
docker compose up --build -d gateway backend
docker compose ps
```

说明：首次启动时网关会自动生成一张“临时自签证书”用于占位，避免 Nginx 因证书不存在而无法启动。

## 5. 首次签发正式证书

执行签发（HTTP-01）：

```bash
docker compose --profile certbot run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d smartinterview.cn \
  -m 2377963631@qq.com \
  --agree-tos --no-eff-email
```

签发成功后重载网关：

```bash
docker compose exec gateway nginx -t
docker compose exec gateway nginx -s reload
```

## 6. 上线验证

1. ACME 路径可达（404 可接受）：

```bash
curl -I http://smartinterview.cn/.well-known/acme-challenge/test
```

2. HTTP 自动跳转 HTTPS：

```bash
curl -I http://smartinterview.cn
```

预期：`301` 且 `Location: https://smartinterview.cn/...`

3. HTTPS 可用：

```bash
curl -I https://smartinterview.cn
```

4. 证书有效期：

```bash
openssl s_client -connect smartinterview.cn:443 -servername smartinterview.cn </dev/null 2>/dev/null | openssl x509 -noout -dates
```

5. 页面访问：

- `https://smartinterview.cn/`
- `https://smartinterview.cn/admin/login`
- `https://smartinterview.cn/check-in?token=INT-xxxx`

## 7. 自动续订（cron）

编辑 crontab：

```bash
crontab -e
```

添加每日任务（凌晨 3 点）：

```cron
0 3 * * * cd /path/to/demohouse/live_voice_call && docker compose --profile certbot run --rm certbot renew --webroot -w /var/www/certbot && docker compose exec -T gateway nginx -s reload >> /var/log/live-voice-certbot-renew.log 2>&1
```

首次加完后执行一次 dry-run：

```bash
docker compose --profile certbot run --rm certbot renew --dry-run --webroot -w /var/www/certbot
docker compose exec gateway nginx -s reload
```

## 8. 常用运维命令

```bash
# 状态
docker compose ps

# 日志
docker compose logs -f gateway
docker compose logs -f backend

# 重启
docker compose restart

# 停止
docker compose down

# 重建并启动
docker compose up --build -d
```

## 9. 常见问题

### 9.1 certbot 签发失败

常见原因：

- 域名未正确解析到 ECS
- 80 端口未放通
- 域名被 CDN 代理且未放行 HTTP-01

排查：

```bash
docker compose logs -f gateway
docker compose --profile certbot run --rm certbot certonly --webroot -w /var/www/certbot -d smartinterview.cn -m 2377963631@qq.com --agree-tos --no-eff-email -v
```

### 9.2 后端容器反复退出

后端启动前会执行 LLM/ASR/TTS 自检，失败会直接退出：

```bash
docker compose logs -f backend
```

修复 `.env` 中对应凭据后再重启。
