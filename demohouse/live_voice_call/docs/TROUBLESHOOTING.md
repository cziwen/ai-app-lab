# 常见问题排查手册

## 概述

本文档提供语音面试系统的完整故障排查指南,涵盖启动失败、连接问题、音频问题、性能问题和日志分析方法。

## 启动失败排查

### 1. 后端容器反复退出

**症状**:
```bash
$ docker compose ps
NAME                           STATUS
live_voice_call_backend        Exited (1)
live_voice_call_gateway        Up
```

**原因**: 启动自检失败 (LLM1/LLM2/ASR/TTS 任一失败即退出)

**排查步骤**:

#### 步骤 1: 查看后端日志
```bash
docker compose logs -f backend
```

**关键日志**:
```
[StartupSelfCheck] LLM1 check failed: invalid API key
[StartupSelfCheck] ASR check failed: resource_id not found
[StartupSelfCheck] TTS check failed: speaker not available
```

#### 步骤 2: 检查环境变量
```bash
# 查看 .env 文件
cat .env | grep -E "ARK_API_KEY|LLM1_ENDPOINT_ID|LLM2_ENDPOINT_ID|ASR_|TTS_"

# 检查必填字段
必填项:
- ARK_API_KEY          # 火山方舟 API 密钥
- LLM1_ENDPOINT_ID     # Judge LLM 端点
- LLM2_ENDPOINT_ID     # Interviewer LLM 端点
- ASR_APP_ID           # ASR 应用 ID
- ASR_ACCESS_TOKEN     # ASR 访问令牌
- ASR_RESOURCE_ID      # ASR 资源 ID (如 volc.bigasr.sauc.duration)
- TTS_APP_ID           # TTS 应用 ID
- TTS_ACCESS_TOKEN     # TTS 访问令牌
- TTS_SPEAKER          # 发音人 ID
```

#### 步骤 3: 验证凭据有效性

**LLM 验证**:
```bash
# 测试 LLM 端点
curl -X POST https://ark.cn-beijing.volces.com/api/v3/chat/completions \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'$LLM1_ENDPOINT_ID'",
    "messages": [{"role": "user", "content": "Hi"}]
  }'

# 预期: 返回 200 和 completion 结果
# 失败: 返回 401 (无效 API Key) 或 404 (端点不存在)
```

**ASR 验证**:
```bash
# 检查 ASR 配置
# (需通过火山引擎控制台验证 ASR_RESOURCE_ID 是否正确)
# 常见错误: volc.bigasr.sauc.duration 拼写错误
```

**TTS 验证**:
```bash
# 检查发音人 ID
# 登录火山引擎语音技术控制台 -> 语音合成大模型 -> 查看可用音色
# 常见音色:
# - zh_female_tianmei_moon_bigtts
# - zh_female_sajiaonvyou_moon_bigtts
# - zh_male_qingse_moon_bigtts
```

#### 步骤 4: 修复并重启

```bash
# 修复 .env 文件
vim .env

# 重启后端容器
docker compose up -d backend

# 实时查看启动日志
docker compose logs -f backend

# 看到 "WebSocket server started at ws://0.0.0.0:8888" 表示启动成功
```

### 2. 服务器日志显示 SDK 版本错误

**症状**:
```
AttributeError: 'AsyncArk' object has no attribute 'responses'
```

**原因**: volcengine-python-sdk 版本过旧 (需要 >= 5.0.19)

**解决方案**:

```bash
# 方案 1: 重建后端镜像 (推荐)
docker compose up -d --build backend

# 方案 2: 手动更新容器内 SDK
docker compose exec backend pip install --upgrade volcengine-python-sdk

# 方案 3: 验证 SDK 版本
docker compose exec backend python -c "
import importlib.metadata as m
from volcenginesdkarkruntime import AsyncArk
c = AsyncArk(base_url='https://ark.cn-beijing.volces.com/api/v3', api_key='x')
print('SDK version:', m.version('volcengine-python-sdk'))
print('Has responses attr:', hasattr(c, 'responses'))
"

# 预期输出:
# SDK version: 5.0.19
# Has responses attr: True
```

### 3. 前端构建失败 (OOM)

**症状**:
```
FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory
```

**原因**: 前端构建内存不足 (默认 Node.js 堆限制 512MB)

**解决方案**:

```bash
# 方案 1: 增加 Node 堆内存
export FRONTEND_NODE_OPTIONS=--max-old-space-size=1024
docker compose up -d --build gateway

# 方案 2: 启用低内存构建模式
./deploy/ssl.sh init  # 自动启用串行构建和 swap

# 方案 3: 手动开启 swap (如未开启)
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### 4. Nginx 启动失败

**症状**:
```bash
$ docker compose logs gateway
nginx: [emerg] cannot load certificate "/etc/letsencrypt/live/__active__/fullchain.pem"
```

**原因**: SSL 证书未生成

**解决方案**:

```bash
# 方案 1: 初始化 HTTPS (自动申请证书)
./deploy/ssl.sh init

# 方案 2: 手动激活证书
./deploy/ssl.sh activate

# 方案 3: 检查证书软链
ls -l deploy/letsencrypt/live/__active__
# 预期: 软链指向 smartinterview.cn 目录

# 方案 4: 临时使用 HTTP (不推荐生产环境)
# 修改 docker-compose.yml 注释掉 443 端口映射
```

## 连接问题排查

### 1. WebSocket 连接失败

**症状**: 前端控制台显示 `WebSocket connection failed`

**排查步骤**:

#### 步骤 1: 检查 token 有效性
```bash
# 查询 token 是否存在
sqlite3 backend/data/storage/interviews.db \
  "SELECT * FROM interviews WHERE interview_token='INT-xxx';"

# 预期: 返回面试记录
# 失败: 返回空 (token 不存在或已过期)
```

#### 步骤 2: 检查 WebSocket 服务状态
```bash
# 检查后端容器是否运行
docker compose ps backend

# 检查 8888 端口监听
docker compose exec backend netstat -tuln | grep 8888

# 测试 WebSocket 连接
wscat -c "ws://localhost:8888?token=INT-test"
# 预期: 收到 BotReady 事件
```

#### 步骤 3: 检查 Nginx 代理配置
```bash
# 查看 Nginx 配置
cat deploy/nginx.conf | grep -A 10 "location /ws"

# 关键配置:
# - proxy_pass http://backend:8888;
# - proxy_http_version 1.1;
# - proxy_set_header Upgrade $http_upgrade;
# - proxy_set_header Connection "upgrade";

# 测试代理转发
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  http://localhost/ws?token=INT-test

# 预期: 返回 101 Switching Protocols
```

#### 步骤 4: 检查防火墙和安全组
```bash
# ECS 部署: 检查安全组是否放通 80/443
# 本地部署: 检查防火墙规则
sudo ufw status | grep -E "80|443"
```

### 2. 前端页面显示 Invalid Link

**症状**: 访问 `/check-in?token=xxx` 显示无效链接页面

**原因分析**:

1. **token 无效**: 数据库中不存在该 token
2. **session 限制**: 非 localhost 环境使用 `session=s1` 参数
3. **token 过期**: 面试已结束或链接已失效

**排查方法**:

```bash
# 1. 验证 token
sqlite3 backend/data/storage/interviews.db \
  "SELECT interview_token, job_title, status, created_at
   FROM interviews
   WHERE interview_token='INT-xxx';"

# 2. 检查访问限制逻辑 (前端代码)
# frontend/src/routes/page.tsx
# 规则: session=s1 仅允许 localhost/127.0.0.1/::1

# 3. 创建测试 token (通过管理后台)
# http://localhost:8080/admin/jobs -> 创建岗位 -> 生成面试链接
```

### 3. 前端登录管理后台失败 (socket hang up / ECONNRESET)

**症状**:
```
Error: socket hang up
Error: read ECONNRESET
```

**原因**: 本机其他程序占用 8890 端口 (Admin API 端口)

**排查步骤**:

```bash
# 1. 检查 8890 端口占用
lsof -i :8890
# 或
netstat -tuln | grep 8890

# 2. 如有占用,停止冲突进程
sudo kill -9 <PID>

# 3. 或更改 Admin API 端口
export ADMIN_API_PORT=18990
docker compose restart backend

# 4. 更新前端代理配置
export ADMIN_API_PROXY_TARGET=http://127.0.0.1:18990
cd frontend && pnpm dev
```

### 4. 排队一直不推进

**症状**: 前端显示 "排队中,位置: 3" 长时间不变化

**排查步骤**:

```bash
# 1. 查看当前活跃面试数
grep "interview.started" logs/backend-*.log | tail -10
grep "interview.closed" logs/backend-*.log | tail -10

# 开始数 = 结束数 + 活跃数
# 如果 (开始数 - 结束数) < MAX_ACTIVE_INTERVIEWS,说明有空位但未释放

# 2. 检查是否有僵尸会话
docker compose exec backend ps aux | grep "python.*handler"

# 3. 查看排队状态
grep "QueueUpdate" logs/backend-*.log | tail -5

# 4. 强制重启 (清理僵尸会话)
docker compose restart backend
```

## 音频问题排查

### 1. 候选人音频无法录制

**症状**: 前端显示麦克风已授权,但后端无法识别语音

**排查步骤**:

#### 步骤 1: 检查浏览器权限
```javascript
// 前端控制台执行
navigator.mediaDevices.getUserMedia({audio: true})
  .then(stream => {
    console.log("Mic permission granted");
    console.log("Audio tracks:", stream.getAudioTracks());
  })
  .catch(err => console.error("Mic permission denied:", err));
```

#### 步骤 2: 检查音频编码
```javascript
// 查看 MediaRecorder 支持的编码
MediaRecorder.isTypeSupported('audio/webm')  // 应返回 true

// 检查录制参数
// frontend/src/components/AudioChatServiceProvider/hooks/useAudioRecorder.ts
// mimeType: 'audio/webm'
// audioBitsPerSecond: 128000
```

#### 步骤 3: 检查后端接收
```bash
# 查看后端日志
grep "USER_AUDIO" backend/data/storage/interview_logs/$token/backend.log

# 预期: 每 100ms 收到一次 UserAudio 事件
# 失败: 无日志或 "drop candidate audio frame" 错误
```

#### 步骤 4: 检查 ASR 连接
```bash
# 查看 ASR 初始化日志
grep "ASR" backend/data/storage/interview_logs/$token/backend.log

# 关键日志:
# - "ASR client initialized"
# - "ASR stream started"
# - "ASR_INIT_FATAL" (失败标志)
```

**常见错误与解决**:

| 错误 | 原因 | 解决方案 |
|-----|------|---------|
| `drop candidate audio frame: legacy nested payload` | 使用旧协议格式 | 更新前端代码到最新版本 |
| `drop candidate audio frame: invalid pcm payload size` | PCM 格式错误 (非偶数字节) | 检查前端音频转换逻辑 |
| `ASR_INIT_UNAVAILABLE` | ASR 服务不可用 | 检查 ASR 凭据和网络连通性 |
| `ASR_RESOURCE_ID not found` | ASR_RESOURCE_ID 配置错误 | 确认使用 `volc.bigasr.sauc.duration` |

### 2. 面试官音频无法播放

**症状**: 前端收到 TTS 事件,但无声音

**排查步骤**:

#### 步骤 1: 检查浏览器 AudioContext 状态
```javascript
// 前端控制台执行
console.log("AudioContext state:", audioContext.state);
// 预期: "running"
// 失败: "suspended" (需用户交互解锁)

// 手动解锁
audioContext.resume().then(() => {
  console.log("AudioContext unlocked");
});
```

#### 步骤 2: 检查音频路由模式
```javascript
// 查看当前路由模式
// 前端日志: [AudioRuntime] audio init route=web-audio-fallback

// iOS Safari: 应使用 media-element 模式
// 其他浏览器: 应使用 web-audio-fallback 模式
```

#### 步骤 3: 检查音频数据接收
```javascript
// 监听 WebSocket 消息
websocket.onmessage = (event) => {
  const decoded = decodeWebSocketResponse(event.data);
  if (decoded.messageType === SERVER_AUDIO_ONLY_RESPONSE) {
    console.log("Received audio chunk:", decoded.payload.byteLength);
  }
};

// 预期: 持续收到音频块
// 失败: 无音频块或 "decodeAudioData failed" 错误
```

#### 步骤 4: 检查音频格式
```bash
# 后端 TTS 输出格式: MP3
# 检查 TTS 配置
grep "TTS_" .env

# 确认编码格式
# encoding: "mp3"  (默认)
```

**常见错误与解决**:

| 错误 | 原因 | 解决方案 |
|-----|------|---------|
| AudioContext suspended | 浏览器自动播放策略限制 | 用户首次交互后调用 `unlockAudio()` |
| decodeAudioData failed | 音频格式不支持 | 检查浏览器是否支持 MP3 解码 |
| audio route switched to web-audio-fallback | media-element 播放失败 | iOS Safari 权限问题,检查页面配置 |
| No audio output | 系统音量静音或输出设备错误 | 检查设备扬声器配置 |

### 3. 音频播放卡顿或延迟

**症状**: 音频断断续续,或明显延迟

**排查步骤**:

#### 步骤 1: 检查网络延迟
```bash
# 测试到后端的延迟
ping localhost  # 本地部署
ping <ECS_IP>   # 云端部署

# 检查 WebSocket 延迟
# 前端日志: [AudioRuntime] audio chunk played with web-audio-fallback route
# 查看时间戳间隔是否稳定
```

#### 步骤 2: 检查音频队列堆积
```javascript
// 前端控制台
console.log("Audio queue depth:", voiceBotService.audioChunks.length);

// 正常: 0-2
// 异常: > 5 (说明播放速度慢于接收速度)
```

#### 步骤 3: 检查 CPU 占用
```bash
# 容器 CPU 使用
docker stats live_voice_call_backend

# 如果 CPU > 90%,可能是解码性能瓶颈
```

**优化方案**:

```javascript
// 方案 1: 降低 FFT 大小 (减少分析开销)
analyser.fftSize = 512;  // 默认 1024

// 方案 2: 跳帧处理 (队列过深时)
if (audioChunks.length > 5) {
  audioChunks.splice(0, 2);  // 丢弃前 2 帧
  console.warn("Audio queue overflow, dropped 2 frames");
}

// 方案 3: 切换到 media-element 模式 (减少解码开销)
setAudioRouteMode('media-element');
```

### 4. 音量指示器不动

**症状**: 面试官说话时音量指示器无变化

**原因**: 使用 media-element 模式,无法实时分析音频波形

**解决方案**:

```javascript
// 检查音频路由模式
console.log("Audio route mode:", voiceBotService.audioRouteMode);

// 如果是 media-element:
// - iOS Safari: 正常行为 (无法实时分析)
// - 其他浏览器: 应切换到 web-audio-fallback

// 强制切换模式
voiceBotService.setAudioRouteMode('web-audio-fallback');
```

## 性能问题排查

### 1. 回复延迟过高 (> 8 秒)

**排查步骤**:

```bash
# 1. 获取 TurnTrace JSON 日志（按 event 过滤）
token="INT-xxx"
log_path="backend/data/storage/interview_logs/$token/backend.log"
grep "TurnTrace" "$log_path" | grep '"event": "turn_latency_breakdown"'

# 2. 分析各阶段耗时（当前日志为 JSON，不再是 key=value）
grep "TurnTrace" "$log_path" | grep '"event": "turn_latency_breakdown"' | grep -o '"judge_ms": [0-9]*'
grep "TurnTrace" "$log_path" | grep '"event": "turn_latency_breakdown"' | grep -o '"llm2_ttft_ms": [0-9]*'
grep "TurnTrace" "$log_path" | grep '"event": "turn_latency_breakdown"' | grep -o '"rec_to_first_sentence_ms": [0-9]*'

# 3. 单题上下文长度（用于判断上下文是否膨胀）
grep "LLM #2 context stats" "$log_path"
grep "TurnTrace" "$log_path" | grep '"event": "interviewer_llm_start"' | grep '"segment_context_len"'
```

**快速修复**:

```bash
# 临时降低 LLM 推理强度
export LLM1_THINKING_TYPE=disabled
export LLM2_THINKING_TYPE=disabled
export LLM1_REASONING_EFFORT=minimal
export LLM2_REASONING_EFFORT=minimal

# 增加 LLM 并发数
export LLM_CONCURRENT_REQUESTS=10

# 重启服务
docker compose restart backend
```

详见: [性能优化指南](PERFORMANCE.md#延迟优化策略)

### 2. 并发面试数达到上限后新用户无法进入

**症状**: 活跃面试数 = MAX_ACTIVE_INTERVIEWS,新用户一直排队

**排查步骤**:

```bash
# 1. 检查活跃面试数
grep "interview.started" logs/backend-*.log | tail -20
grep "interview.closed" logs/backend-*.log | tail -20

# 开始 - 结束 = 当前活跃数
# 如果结果 >= MAX_ACTIVE_INTERVIEWS,说明达到上限

# 2. 查看是否有长时间未结束的面试
grep "interview.started" logs/backend-*.log | \
  awk '{print $3, $4}' | \
  while read date time; do
    timestamp=$(date -d "$date $time" +%s)
    now=$(date +%s)
    duration=$((now - timestamp))
    if [ $duration -gt 1800 ]; then  # 超过 30 分钟
      echo "Long-running interview: $date $time (${duration}s)"
    fi
  done

# 3. 检查是否有僵尸连接
docker compose exec backend ps aux | grep python
```

**解决方案**:

```bash
# 方案 1: 增加并发限制 (需评估资源)
export MAX_ACTIVE_INTERVIEWS=10
docker compose restart backend

# 方案 2: 强制结束长时间面试 (需手动)
# 找到对应 token,删除数据库记录
sqlite3 backend/data/storage/interviews.db \
  "UPDATE interviews SET status='disconnected' WHERE interview_token='INT-xxx';"

# 方案 3: 重启服务 (清理所有僵尸连接)
docker compose restart backend
```

### 3. 内存占用持续增长

**症状**: 容器内存使用率持续上升,直到 OOM

**排查步骤**:

```bash
# 1. 监控容器内存
docker stats live_voice_call_backend --no-stream

# 2. 检查 Python 进程内存
docker compose exec backend ps aux --sort=-%mem | head -10

# 3. 检查 Logger 缓存
grep "interview_logger_cache" logs/backend-*.log | tail -10

# 4. 检查持久化队列
grep "persistence_queue" logs/backend-*.log | tail -10
```

**常见内存泄漏原因**:

| 原因 | 排查方法 | 解决方案 |
|-----|---------|---------|
| Logger 缓存未释放 | `_INTERVIEW_LOGGER_CACHE` 大小持续增长 | 降低 `INTERVIEW_LOGGER_IDLE_SECONDS` |
| 音频数据未释放 | `candidate_audio` / `interviewer_audio` 未清空 | 确保持久化后清空缓冲区 |
| WebSocket 连接未关闭 | 活跃连接数远超 MAX_ACTIVE_INTERVIEWS | 增加连接超时时间限制 |
| TTS/ASR 客户端泄漏 | 客户端实例未 close | 确保 finally 块中关闭客户端 |

**临时解决**:

```bash
# 重启服务释放内存
docker compose restart backend

# 调整配置 (永久修复)
export INTERVIEW_LOGGER_CACHE_MAX=500
export INTERVIEW_LOGGER_IDLE_SECONDS=600
docker compose up -d backend
```

## 日志分析指南

建议先执行下面的“最小命令集（复制即用）”，确认会话状态、关键耗时和错误类型；只有需要深挖时再看后面的详细章节。

### 日志文件位置

```
backend/logs/
├── backend-20250322-124530-p12345.log  # 服务器日志 (每次启动新文件)

backend/data/storage/interview_logs/
├── INT-abc123/
│   ├── backend.log    # 单场面试后端日志
│   └── frontend.log   # 单场面试前端日志
└── INT-def456/
    ├── backend.log
    └── frontend.log
```

### 最小关键日志视图（grep 速查）

1. 服务器级（全局状态）: `logs/backend-*.log`
2. 单面试级（单 token 轨迹）: `backend/data/storage/interview_logs/$token/backend.log`
3. 错误级（快速定位异常）: `ERROR|Exception|ASR_INIT|LLM.*error`

### 最小命令集（复制即用）

```bash
# Step 1) 先定位 token（最近开始/结束的会话）
grep "event=interview.started" logs/backend-*.log | tail -20
grep "event=interview.closed" logs/backend-*.log | tail -20

# Step 2) 指定 token 看单场关键日志
token="INT-xxx"
log_path="backend/data/storage/interview_logs/$token/backend.log"

# 2.1 Judge 决策 + LLM2 调用 + TurnTrace 关键事件
grep "Judge result:" "$log_path"
grep "LLM #2 context stats" "$log_path"
grep "TurnTrace" "$log_path" | grep '"event": "interviewer_llm_start"'
grep "TurnTrace" "$log_path" | grep '"event": "turn_latency_breakdown"'

# 2.2 单题上下文长度（新增）
grep "LLM #2 context stats" "$log_path" | tail -20
grep "TurnTrace" "$log_path" | grep '"event": "interviewer_llm_start"' | grep '"segment_context_len"'

# Step 3) 最后看错误
grep -i "ERROR\|Exception" "$log_path"
grep "ASR_INIT" "$log_path"
grep "LLM.*error\|LLM.*timeout" "$log_path"
```

单题上下文长度字段说明：
- `context_len`: 当轮传给 LLM2 的“指令文本”长度。
- `segment_context_len`: 当前问题/场景段累计上下文长度（包含追问与同场景子问题历史）。

### 服务器日志 (backend-*.log)

**用途**: 全局事件、配置、面试会话管理

**关键日志（按需）**:

```bash
# 1. 服务启动
grep "event=server.startup" logs/backend-*.log

# 2. 配置加载
grep "event=server.config" logs/backend-*.log

# 3. 自检结果
grep "startup_self_check" logs/backend-*.log

# 4. 面试开始/结束
grep "event=interview.started" logs/backend-*.log
grep "event=interview.closed" logs/backend-*.log

# 5. 准入控制
grep "QueueEntered\|QueueUpdate\|QueueAdmitted" logs/backend-*.log

# 6. 持久化事件
grep "interview_persist" logs/backend-*.log
```

### 面试后端日志 (backend.log)

**用途**: 单场面试的完整执行轨迹

**关键日志（按需）**:

```bash
token="INT-xxx"
log_path="backend/data/storage/interview_logs/$token/backend.log"

# 1. 会话生命周期
grep "Interview session" $log_path

# 2. ASR 识别
grep "Candidate answer:" $log_path

# 3. Judge 决策
grep "Judge result:" $log_path

# 4. LLM2 生成
grep "Interviewer LLM:" $log_path
grep "LLM #2 context stats" $log_path

# 5. TTS 播放
grep "TTS:" $log_path

# 6. 性能指标
grep "TurnTrace" $log_path
grep "TurnTrace" $log_path | grep '"event": "turn_latency_breakdown"'
grep "TurnTrace" $log_path | grep '"event": "interviewer_llm_start"'

# 7. 错误日志
grep "ERROR\|Exception" $log_path
```

### 面试前端日志 (frontend.log)

**用途**: 浏览器端事件、错误、用户交互

**关键日志**:

```bash
token="INT-xxx"
log_path="backend/data/storage/interview_logs/$token/frontend.log"

# 1. WebSocket 连接
grep "ws connected\|ws closed" $log_path

# 2. 音频事件
grep "AudioRuntime" $log_path

# 3. 设备检测
grep "device check" $log_path

# 4. 用户交互
grep "user action" $log_path

# 5. 前端错误
grep "error\|Error" $log_path
```

### 日志分析工具

#### 1. 快速诊断脚本

```bash
#!/bin/bash
# diagnose_interview.sh <token>

TOKEN=$1
BACKEND_LOG="backend/data/storage/interview_logs/$TOKEN/backend.log"
FRONTEND_LOG="backend/data/storage/interview_logs/$TOKEN/frontend.log"

if [ ! -f "$BACKEND_LOG" ]; then
    echo "Interview log not found: $TOKEN"
    exit 1
fi

echo "=== Interview Diagnosis: $TOKEN ==="

# 1. 基本信息
echo -e "\n[Basic Info]"
echo "Start time: $(grep "Interview session started" $BACKEND_LOG | head -1 | awk '{print $1, $2}')"
echo "End time: $(grep "Interview session" $BACKEND_LOG | tail -1 | awk '{print $1, $2}')"
echo "Total turns: $(grep "TurnTrace" "$BACKEND_LOG" | grep '"event": "turn_latency_breakdown"' | wc -l)"

# 2. 完成状态
echo -e "\n[Status]"
if grep -q "interview_completed" $BACKEND_LOG; then
    echo "Status: Completed"
else
    echo "Status: Disconnected or In Progress"
fi

# 3. 性能指标
echo -e "\n[Performance]"
echo "Average latency:"
grep "TurnTrace" "$BACKEND_LOG" | \
  grep '"event": "turn_latency_breakdown"' | \
  grep -o '"rec_to_first_sentence_ms": [0-9]*' | \
  awk '{sum+=$2; if($2>max) max=$2} END {if (NR==0) {print "  no data"} else {print "  Avg:", sum/NR "ms, Max:", max "ms"}}'

# 4. 错误统计
echo -e "\n[Errors]"
echo "Backend errors: $(grep -i "error\|exception" $BACKEND_LOG | wc -l)"
if [ -f "$FRONTEND_LOG" ]; then
    echo "Frontend errors: $(grep -i "error" $FRONTEND_LOG | wc -l)"
fi

# 5. 异常轮次
echo -e "\n[Slow Turns (>8s)]"
grep "TurnTrace" "$BACKEND_LOG" | \
  grep '"event": "turn_latency_breakdown"' | \
  awk '{
      tid=""; lat="";
      if (match($0, /"turn_id": "[^"]+"/)) { tid=substr($0, RSTART+12, RLENGTH-13) }
      if (match($0, /"rec_to_first_sentence_ms": [0-9]+/)) { lat=substr($0, RSTART+28, RLENGTH-28) }
      if (lat != "" && lat+0 > 8000) print "  Turn " tid ": " lat "ms"
  }'

# 6. ASR 问题
echo -e "\n[ASR Issues]"
grep "ASR_INIT\|drop candidate audio" $BACKEND_LOG | wc -l | xargs echo "ASR errors:"

# 7. LLM 问题
echo -e "\n[LLM Issues]"
grep "LLM.*timeout\|LLM.*error" $BACKEND_LOG | wc -l | xargs echo "LLM errors:"
```

#### 2. 性能对比分析

```bash
#!/bin/bash
# compare_interviews.sh <token1> <token2>

TOKEN1=$1
TOKEN2=$2

echo "=== Performance Comparison ==="
echo "Token 1: $TOKEN1"
echo "Token 2: $TOKEN2"

for token in $TOKEN1 $TOKEN2; do
    log="backend/data/storage/interview_logs/$token/backend.log"
    echo -e "\n[$token]"

    # 平均延迟
    avg=$(grep "TurnTrace" "$log" | grep '"event": "turn_latency_breakdown"' | \
      grep -o '"rec_to_first_sentence_ms": [0-9]*' | awk '{sum+=$2} END {if (NR==0) print "N/A"; else print sum/NR}')
    echo "Avg latency: ${avg}ms"

    # Judge 延迟
    judge_avg=$(grep "TurnTrace" "$log" | grep '"event": "turn_latency_breakdown"' | \
      grep -o '"judge_ms": [0-9]*' | awk '{sum+=$2} END {if (NR==0) print "N/A"; else print sum/NR}')
    echo "Judge avg: ${judge_avg}ms"

    # LLM2 TTFT
    ttft_avg=$(grep "TurnTrace" "$log" | grep '"event": "turn_latency_breakdown"' | \
      grep -o '"llm2_ttft_ms": [0-9]*' | awk '{sum+=$2} END {if (NR==0) print "N/A"; else print sum/NR}')
    echo "LLM2 TTFT avg: ${ttft_avg}ms"
done
```

#### 3. 错误模式分析

```python
# error_pattern_analysis.py
import re
from collections import Counter

def analyze_errors(log_path):
    error_patterns = Counter()

    with open(log_path) as f:
        for line in f:
            if "ERROR" in line or "Exception" in line:
                # 提取错误类型
                match = re.search(r'(Error|Exception): (.+)', line)
                if match:
                    error_type = match.group(2).split()[0]
                    error_patterns[error_type] += 1

    print("Error frequency:")
    for error, count in error_patterns.most_common(10):
        print(f"  {error}: {count}")

# 使用示例
analyze_errors("backend/data/storage/interview_logs/INT-xxx/backend.log")
```

### 日志查询常用命令

```bash
# 1. 查询所有今天的面试
find backend/data/storage/interview_logs -name "backend.log" -mtime -1

# 2. 查询失败的面试
grep -l "ERROR.*ASR_INIT_UNAVAILABLE" backend/data/storage/interview_logs/*/backend.log

# 3. 查询慢面试 (平均延迟 > 6s)
for log in backend/data/storage/interview_logs/*/backend.log; do
    avg=$(grep "TurnTrace" "$log" | grep '"event": "turn_latency_breakdown"' | \
      grep -o '"rec_to_first_sentence_ms": [0-9]*' | awk '{sum+=$2} END {if (NR==0) print "0"; else print sum/NR}')
    if [ $(echo "$avg > 6000" | bc) -eq 1 ]; then
        echo "$log: ${avg}ms"
    fi
done

# 4. 统计每天的面试数
for day in $(seq 0 7); do
    count=$(find backend/data/storage/interview_logs -name "backend.log" -mtime -$day | wc -l)
    date=$(date -d "-$day days" +%Y-%m-%d)
    echo "$date: $count interviews"
done

# 5. 查询特定时间段的面试
start_time="2025-03-22 10:00:00"
end_time="2025-03-22 12:00:00"

find backend/data/storage/interview_logs -name "backend.log" | while read log; do
    first_line=$(head -1 $log)
    log_time=$(echo $first_line | awk '{print $1, $2}')
    if [[ "$log_time" > "$start_time" && "$log_time" < "$end_time" ]]; then
        echo $log
    fi
done
```

## 快速故障自检清单

```
□ 后端服务启动正常 (docker compose ps)
  ├─ backend: Up
  └─ gateway: Up

□ 自检通过 (grep "startup_self_check" logs/backend-*.log)
  ├─ LLM1: OK
  ├─ LLM2: OK
  ├─ ASR: OK
  └─ TTS: OK

□ WebSocket 可连接 (wscat -c "ws://localhost:8888?token=test")

□ 前端页面可访问 (http://localhost:8080)

□ 管理后台可登录 (http://localhost:8080/admin/login)

□ 音频录制正常 (浏览器麦克风权限已授予)

□ 音频播放正常 (AudioContext state = "running")

□ 延迟在合理范围 (rec_to_first_sentence_ms < 6000ms)

□ 无内存泄漏 (docker stats 显示内存稳定)

□ 日志无 ERROR (grep ERROR logs/backend-*.log)
```

## 相关文档

- [系统架构](ARCHITECTURE.md)
- [数据流向详解](DATA_FLOW.md)
- [性能优化指南](PERFORMANCE.md)
- [部署指南](../deploy/DEPLOY.md)
