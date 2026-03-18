# HTTPS 部署（简化版）

域名：`smartinterview.cn`  
目标：一个脚本完成初始化、续订、安装定时任务。

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
- 业务凭据 `ARK_API_KEY`、`LLM_ENDPOINT_ID`、`ASR_*`、`TTS_*`

## 第二步：一键初始化 HTTPS

```bash
./deploy/ssl.sh init
```

这一步会自动完成：

- 构建并启动 `gateway/backend`
- 申请 Let’s Encrypt 证书
- 自动切换活动证书软链 `deploy/letsencrypt/live/__active__`
- 校验并重载 Nginx

## 第三步：安装自动续订

```bash
./deploy/ssl.sh install-cron
```

会安装每日 03:00 续订任务（主机 cron）：

- 执行 `./deploy/ssl.sh renew`
- 成功后自动重载 Nginx

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
