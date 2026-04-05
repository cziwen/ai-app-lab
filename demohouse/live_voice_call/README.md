# 语音实时通话 - 青青

## 应用介绍
这款实时语音通话应用，依托豆包语音系列大模型打造。在这里，用户能与虚拟好友乔青青展开模拟通话，畅享真实的交流体验。
乔青青（Doreen），一位 20 岁的射手座新闻传播专业学生，性格率真爽朗、成绩优异，对追星、旅游、唱歌、摄影充满热情。无论是分享日常琐事，还是深入探讨趣味话题，她都能成为你的理想伙伴。而且，用户还能根据喜好，自由选择青青的音色，从清脆甜美的少女音，到活力满满的灵动嗓音，为这场交流增添更多个性化色彩 。
想认识这位多才多艺、热情开朗的青青吗？快来加入她的世界，与她一同探索生活的精彩，追逐梦想的光芒。

### 效果预览

[视频地址](https://portal.volccdn.com/obj/volcfe/cloud-universal-doc/upload_8c19a086a04ea8097bab34a5cf552e98.mp4)

### 直接体验

[控制台体验](https://console.volcengine.com/ark/region:ark+cn-beijing/application/detail?id=bot-20240805115108-nx28f-nocode-preset)

### 优势

- 沉浸式真人对话体验：生活化的沟通方式，让使用者在与乔青青交流时，仿佛她就在身边，能真切感受到她的爽朗性格，全方位模拟真人互动，深度沉浸。
- 低延时：实现近乎实时的对话响应，彻底告别长时间等待，高度模拟面对面的真实通话体验。
- WebSocket 方案：易于实现和部署，通用性强跨平台兼容性好，高效利用资源，开发灵活性高，成本低。

### 相关模型

本项目采用双 LLM 架构设计：
- **Doubao-pro-32k (LLM #1 - Judge)**：评判候选人回答质量，决定是否需要追问
- **Doubao-pro-32k (LLM #2 - Interviewer)**：生成自然流畅的面试官对话内容
- **Doubao-语音合成 (TTS)**：根据用户偏好的音色定制生成拟人化、逼真的角色语音输出
- **Doubao-流式语音识别 (ASR)**：基于 SAUC 协议的大模型语音识别，实时转写用户语音

### 系统架构

#### 核心组件

1. **双 LLM 架构**
   - **Judge LLM (LLM #1)**：基于评分标准评判回答质量，决定追问策略
   - **Interviewer LLM (LLM #2)**：生成自然对话内容，保持面试流畅性

2. **面试流程控制**
   - **状态机设计**：INTRO → ASK_QUESTION → WAIT_ANSWER → EVAL_ANSWER → DECIDE → (循环/结束)
   - **智能决策**：根据覆盖度评分自动决定是否追问（阈值 0.7）
   - **轮次限制**：全局候选人发言轮次默认 300（`INTERVIEW_GLOBAL_TURN_LIMIT` 可配置）

3. **并发控制系统**
   - **准入控制**：限制同时进行的面试数量（默认 5 个）
   - **占用过期**：基于 Redis 锁 + TTL 自动回收异常断开的占用
   - **快速失败**：达到并发上限时直接返回“当前面试的人有点多，请稍后再试”

4. **性能监控**
   - **Turn Trace**：详细记录每轮对话的性能指标
   - **关键指标**：judge_ms、llm2_ttft_ms、rec_to_first_sentence_ms
   - **延迟优化**：ASR 静默检测默认 8 秒（`ASR_SILENCE_TIMEOUT_MS` 可配置）、流式 TTS 合成

### 流程架构

本项目的整体流程架构如下：

![img.png](assets/img.png)

## 环境准备

- Poetry 1.6.1 版本
- Python 版本要求大于等于 3.9，小于 3.12
- Node 18.0 或以上版本 
- PNPM 8.10 或以上版本
- 获取语音技术产品的 APP ID 和 Access Token，获取方式参见【附录】
- 火山方舟 API KEY [参考文档](https://www.volcengine.com/docs/82379/1298459#api-key-%E7%AD%BE%E5%90%8D%E9%89%B4%E6%9D%83)
- 火山引擎 AK SK [参考文档](https://www.volcengine.com/docs/6291/65568)
- 创建 Doubao-Pro 32K 的endpoint [参考文档](https://www.volcengine.com/docs/82379/1099522)

## 快速开始

本文为您介绍如何在本地快速部署 live voice call 项目。

1. 下载代码库

    ```shell
    git clone https://github.com/volcengine/ai-app-lab.git
    cd demohouse/live_voice_call
    ```

2. 修改配置（环境变量）

    **核心配置（必填）**：
    ```shell
    # 火山方舟 API 密钥
    export ARK_API_KEY={YOUR_API_KEY}

    # LLM 端点配置（双 LLM 架构）
    export LLM1_ENDPOINT_ID={YOUR_ARK_LLM1_ENDPOINT_ID}  # Judge LLM - 评判回答
    export LLM2_ENDPOINT_ID={YOUR_ARK_LLM2_ENDPOINT_ID}  # Interviewer LLM - 生成对话

    # ASR 配置（SAUC 协议）
    export ASR_APP_ID={YOUR_ASR_APP_ID}
    export ASR_ACCESS_TOKEN={YOUR_ASR_ACCESS_TOKEN}
    export ASR_RESOURCE_ID={YOUR_ASR_RESOURCE_ID}  # 必填，如 volc.bigasr.sauc.duration

    # TTS 配置
    export TTS_APP_ID={YOUR_TTS_APP_ID}
    export TTS_ACCESS_TOKEN={YOUR_TTS_ACCESS_TOKEN}
    export TTS_SPEAKER={YOUR_TTS_SPEAKER}  # 音色配置

    # Redis（必填，后端启动自检强依赖）
    # 本机直跑后端：
    export REDIS_URL=redis://127.0.0.1:6379/0
    ```

    **深度思考配置（可选）**：
    ```shell
    # 控制 LLM 的推理模式（默认 disabled）
    export LLM1_THINKING_TYPE=disabled   # enabled|disabled|auto
    export LLM2_THINKING_TYPE=disabled   # enabled|disabled|auto

    # 推理努力程度（仅在 THINKING_TYPE=enabled 时生效）
    export LLM1_REASONING_EFFORT=minimal  # minimal|low|medium|high
    export LLM2_REASONING_EFFORT=minimal  # minimal|low|medium|high
    ```

    **高级配置（可选）**：
    ```shell
    # ASR WebSocket URL（默认使用官方推荐链路）
    export ASR_WS_URL=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async

    # 并发控制
    export MAX_ACTIVE_INTERVIEWS=5        # 最大同时面试数
    export INTERVIEW_GLOBAL_TURN_LIMIT=300  # 全局候选人发言轮次上限
    export INTERVIEW_OCCUPANCY_TTL_SECONDS=30      # token 占用 TTL（秒）
    export INTERVIEW_OCCUPANCY_HEARTBEAT_SECONDS=10  # 占用续期间隔（秒）
    export INTERVIEW_EXPIRY_SWEEP_SECONDS=10       # 过期 token 扫描周期（秒）
    export INTERVIEW_EXPIRY_SWEEP_BATCH_SIZE=200   # 每轮最多处理的过期 token 数
    export LLM_CONCURRENT_REQUESTS=5      # LLM 并发请求数

    # WebSocket 保活（用于更快回收异常断开的会话占位）
    export WS_PING_INTERVAL_SECONDS=20
    export WS_PING_TIMEOUT_SECONDS=20
    export WS_CLOSE_TIMEOUT_SECONDS=5

    # 管理后台配置
    export ADMIN_USERNAME=admin           # 管理员用户名
    export ADMIN_PASSWORD=admin123456     # 管理员密码
    export ADMIN_CORS_ORIGINS=http://localhost:8080,http://127.0.0.1:8080

    # 公共访问配置（只填域名；系统自动拼接 /check-in?token=...）
    export INTERVIEW_BASE_DOMAIN=http://localhost:8080
    ```

    **日志配置（可选）**：
    ```shell
    # 异步日志队列
    export ASYNC_LOG_QUEUE_SIZE=10000           # 队列大小
    export ASYNC_LOG_FLUSH_INTERVAL_MS=200      # 刷新间隔
    export ASYNC_LOG_DROP_POLICY=drop_oldest    # 丢弃策略
    export ASYNC_LOG_CLOSE_TIMEOUT_SECONDS=5    # 关闭超时

    # 面试日志缓存
    export INTERVIEW_LOGGER_CACHE_MAX=100       # 最大缓存数
    export INTERVIEW_LOGGER_IDLE_SECONDS=1800   # 空闲超时

    # 前端日志限制
    export FRONTEND_LOG_MAX_BODY_BYTES=1048576  # 最大请求体
    export FRONTEND_LOG_MAX_ENTRIES=100         # 最大条目数
    export FRONTEND_LOG_MAX_ENTRY_CHARS=10000   # 单条最大长度
    ```

3. 启动服务端

    先确保本机 Redis 已启动并满足配置要求：

    ```shell
    # 安装（仅首次）
    brew install redis

    # 后台启动 Redis
    brew services start redis

    # 后台关闭 Redis
    # brew services stop redis

    # 限制内存并设置淘汰策略（启动自检会校验）
    redis-cli CONFIG SET maxmemory 268435456
    redis-cli CONFIG SET maxmemory-policy allkeys-lru
    redis-cli CONFIG REWRITE

    # 健康检查
    redis-cli -h 127.0.0.1 -p 6379 ping
    ```

    ```shell
    cd demohouse/live_voice_call/backend

    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install poetry==1.6.1

    poetry install
    poetry run python -m handler
    ```

   若你刚升级后端依赖，请先重建容器镜像再启动：

   ```shell
   docker compose up -d --build backend
   ```

   后端启动前会自动执行一次 `LLM1 + LLM2 + ASR + TTS + Redis` 开机自检。
   若任一依赖不可用（例如未设置 `ARK_API_KEY`），进程会直接退出并打印失败项，不会监听端口。
   ASR 迁移到官方 SAUC 协议后，`ASR_RESOURCE_ID` 为必填项；缺失时自检会直接失败退出。
   Redis 也是强依赖：`REDIS_URL` 缺失、Redis 未启动、或 Redis 配置不满足（`maxmemory=256MB` 且 `maxmemory-policy=allkeys-lru`）都会启动失败。
   若日志出现 `AsyncArk ... no attribute responses`，通常是 `volcengine-python-sdk` 版本过旧（需要 `5.0.19`）。
   默认会同时启动：
   - 面试 WebSocket：`ws://0.0.0.0:8888`
   - 前端日志接收：`http://0.0.0.0:8889/api/frontend-logs?token=INT-...`
   - 管理后台 API：`http://0.0.0.0:8890`

4. 启动web端

    ```shell
    cd demohouse/live_voice_call/frontend
    pnpm install
    pnpm run dev
    ```

   本地开发模式下，前端 `pnpm dev` 已内置代理：`/api/*` 转发到 `8890`、`/api/frontend-logs` 转发到 `8889`、`/ws` 转发到 `8888`。
   如遇到本机其他程序占用 `127.0.0.1:8890`（例如表现为前端登录 `socket hang up` / `ECONNRESET`），可在启动前设置：

   ```shell
   export ADMIN_API_PROXY_TARGET=http://<你的本机IP>:8890
   # 或改成你自定义的 Admin API 端口，例如 http://127.0.0.1:18990
   ```

5. 访问`http://localhost:8080`即可

## Docker Compose 部署（ECS 单机）

1. 准备环境

   - 安装 Docker 与 Docker Compose
   - 打开 ECS 安全组入方向端口：`80`

2. 配置环境变量

    ```shell
    cp .env.example .env
    # 编辑 .env，填入真实凭据与参数
    ```

   Redis 必填配置：

   ```shell
   REDIS_URL=redis://redis:6379/0
   ```

   说明：在 Docker Compose 网络内，`redis` 是服务名；不要写 `127.0.0.1`。

3. 启动服务

    ```shell
    docker compose up --build -d
    ```

4. 验证服务

    ```shell
    curl http://localhost/api/health
    ```

   预期返回：`{"status":"ok"}`

5. 访问入口

   - 应用首页：`http://<ECS_PUBLIC_IP>/`
   - 管理后台登录：`http://<ECS_PUBLIC_IP>/admin/login`
   - 候选人面试页：`http://<ECS_PUBLIC_IP>/check-in?token=...`

### 架构说明（单入口）

- `gateway`（Nginx）对外暴露 `80` 端口
- `backend` 仅在容器网络内暴露：
  - WebSocket：`8888`（通过 `/ws` 代理）
  - 前端日志：`8889`（通过 `/api/frontend-logs` 代理）
  - Admin API：`8890`（通过 `/api/*` 代理）
- `redis` 仅在容器网络内提供缓存服务：
  - 服务端口：`6379`
  - 固定内存上限：`256MB`
  - 淘汰策略：`allkeys-lru`

### 常用运维命令

```shell
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f gateway
docker compose logs -f backend

# 查看本次启动后端文件日志（每次启动新文件）
latest_log=$(ls -t backend/logs/backend-*.log | head -1)
tail -f "$latest_log"

# 重启服务
docker compose restart

# 停止并移除容器
docker compose down
```

### 持久化说明

- 已挂载宿主机目录：
  - `./backend/data:/app/backend/data`
  - `./backend/logs:/app/backend/logs`
- 后端主日志按启动生成：`backend/logs/backend-YYYYmmdd-HHMMSS-p<PID>.log`（不再固定为 `backend.log`）。
- 容器重建后，SQLite 数据与音频/日志文件不会丢失。

### 常见问题排查

- 后端容器反复退出：
  - 原因通常是启动自检失败（LLM1/LLM2/ASR/TTS 任一失败即退出）
  - 用 `docker compose logs -f backend` 查看失败项并修复 `.env`
- 页面可打开但请求失败：
  - 检查安全组是否放通 `80`
  - 检查 `gateway` 是否运行正常：`docker compose logs -f gateway`

## 管理后台

- 登录页：`http://localhost:8080/admin/login`
- 后台页：`http://localhost:8080/admin`
- 默认管理员账号通过环境变量初始化：
  - `ADMIN_USERNAME`（默认 `admin`）
  - `ADMIN_PASSWORD`（默认 `admin123456`）
- 候选人面试链接基址通过 `INTERVIEW_BASE_DOMAIN` 配置（只填域名，例如 `https://smartinterview.cn`）
- 前端可通过 `MODERN_PUBLIC_API_URL` 配置后台 API 地址（默认同源地址）

## WebSocket交互协议说明

### 协议格式

Web端和服务端通过二进制协议进行交互，协议格式如下：

<table>
  <tr>
    <th>Byte \ Bit</th>
    <th>7</th>
    <th>6</th>
    <th>5</th>
    <th>4</th>
    <th>3</th>
    <th>2</th>
    <th>1</th>
    <th>0</th>
  </tr>
  <tr>
    <td>0</td>
    <td colspan="4">Protocol version</td>
    <td colspan="4">Header size</td>
  </tr>
  <tr>
    <td>1</td>
    <td colspan="4">Message type</td>
    <td colspan="4">Message type specific flags</td>
  </tr>
  <tr>
    <td>2</td>
    <td colspan="4">Message serialization method</td>
    <td colspan="4">Message compression</td>
  </tr>
  <tr>
    <td>3</td>
    <td colspan="8">Reserved</td>
  </tr>
  <tr>
    <td>4</td>
    <td colspan="8">[Payload, depending on the Message Type]</td>
  </tr>
  <tr>
    <td>...</td>
    <td colspan="8">...</td>
  </tr>
</table>


前4个字节共32位为Header部分，剩余字节为Payload部分，可以是二进制 或 JSON格式

各部分取值说明如下：

<table>
  <tr>
    <th>Part</th>
    <th>长度</th>
    <th>用途</th>
    <th>取值说明</th>
  </tr>
  <tr>
    <td>Protocol version</td>
    <td>4 bits</td>
    <td>标记协议版本</td>
    <td>固定为 <code>0b0001</code>，代表协议版本 V1</td>
  </tr>
  <tr>
    <td>Header size</td>
    <td>4 bits</td>
    <td>标记 Header 大小</td>
    <td>固定为 <code>0b0001</code>，代表 Header 大小为 1 * 4 个字节</td>
  </tr>
  <tr>
    <td rowspan="5">Message type</td>
    <td rowspan="5">4 bits</td>
    <td rowspan="5">标记消息类型</td>
    <td><code>0b0001</code> - full client request</td>
  </tr>
  <tr>
    <td>&emsp;常规上行请求消息，payload 为 JSON 格式</td>
  </tr>
  <tr>
    <td><code>0b0010</code> - audio only request</td>
  </tr>
  <tr>
    <td>&emsp;语音上行数据消息，payload 为二进制格式</td>
  </tr>
  <tr>
    <td><code>0b1001</code> - full server response</td>
  </tr>
  <tr>
    <td>Message type specific flags</td>
    <td>4 bits</td>
    <td>标记消息附加信息</td>
    <td>目前固定为 <code>0b0000</code>，代表无附加消息</td>
  </tr>
  <tr>
    <td>Message serialization method</td>
    <td>4 bits</td>
    <td>标记 payload 序列化方式</td>
    <td>
      <ul>
        <li><code>0b0000</code> - 无序列化</li>
        <li><code>0b0001</code> - JSON 格式</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td>Message compression</td>
    <td>4 bits</td>
    <td>标记 payload 压缩格式</td>
    <td>
      <ul>
        <li><code>0b0000</code> - 无压缩</li>
        <li><code>0b0001</code> - Gzip 压缩</li>
      </ul>
    </td>
  </tr>
  <tr>
    <td>Reserved</td>
    <td>8 bits</td>
    <td>预留字段</td>
    <td>暂无用途</td>
  </tr>
</table>

### Payload一览

当MessageType为FullClientRequest或FullServerResponse时，Payload部分为JSON数据，格式如下：

```
{
    "event": "事件类型",
    "payload": {
      // 事件内容，根据不同的事件类型区分...
    }
}
```

<table>
    <thead>
        <tr>
            <th>事件类型</th>
            <th>事件方向</th>
            <th>完整Payload</th>
            <th>说明</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>BotReady</td>
            <td>下行</td>
            <td>
                <pre>
{
    "event": "BotReady",
    "payload": {
        "session": "...."
    }
}
                    </pre>
                </td>
                <td>链接建立成功，可以开始对话。如果前端 <code>query</code> 中没有该参数，则会新生成 <code>sid</code> 并返回给前端，用于存储当前对话上下文。</td>
            </tr>
            <tr>
                <td>BotUpdateConfig</td>
                <td>上行</td>
                <td>
                    <pre>
{
    "event": "BotUpdateConfig",
    "payload": {
        "speaker": "...."
    }
}
                    </pre>
                </td>
                <td>用户更新对话上下文信息，例如音色（下一轮生效）。</td>
            </tr>
            <tr>
                <td>SentenceRecognized</td>
                <td>下行</td>
                <td>
                    <pre>
{
    "event": "SentenceRecognized",
    "payload": {
        "sentence": "...."
    }
}
                    </pre>
                </td>
                <td>ASR 成功识别用户语音，返回给前端结果。此事件下发后，连接进入 <code>InProcess</code> 状态，在语音输出结束前，不会再接受新的用户语音输入。</td>
            </tr>
            <tr>
                <td>TTSSentenceStart</td>
                <td>下行</td>
                <td>
                    <pre>
{
    "event": "TTSSentenceStart",
    "payload": {
        "sentence": "...."
    }
}
                    </pre>
                </td>
                <td>当前句子开始语音合成，后续会发送 <code>AudioOnly</code> 事件输出二进制的语音数据。</td>
            </tr>
            <tr>
                <td>TTSDone</td>
                <td>下行</td>
                <td>
                    <pre>
{
    "event": "TTSDone",
    "payload": {}
}
                    </pre>
                </td>
                <td>TTS 语音输出完成。此事件下发后，连接重新恢复 <code>Idle</code> 状态，此时可以开始接受新的用户语音输入。</td>
            </tr>
            <tr>
                <td>BotError</td>
                <td>下行</td>
                <td>
                    <pre>
{
    "event": "BotError",
    "payload": {
        "error": {
            "code": "...",
            "message": "..."
        }
    }
}
                    </pre>
                </td>
                <td>服务端出现错误。</td>
            </tr>
        </tbody>
</table>

### 交互时序示意

![img_1.png](assets/img_1.png)

## 面试题库 CSV v2（必读）

当前题库仅支持 4 列 CSV：

```csv
场景,问题,评分标准,最大分数
```

推荐直接使用模板文件：
`demo_resource/question_bank_v2_template.csv`

填写规则：
- `问题`：每行必填
- `场景`：可空；空值表示“同一场景的后续子问”
- `评分标准/最大分数`：仅场景首问可填；子问必须留空（不是继承）
- 场景切换即新段；同名场景跨段出现也视为新段

运行语义：
- 面试提问按行推进（子问逐条提问）
- LLM2 上下文按“场景连续段”隔离共享
- LLM3 按“场景连续段”整题评分（一段一次调用）
- 评分返回契约固定为：`numeric_score + comment`

## 附录

### 获取 TTS_APP_ID、TTS_ACCESS_TOKEN、ASR_APP_ID、ASR_ACCESS_TOKEN、ASR_RESOURCE_ID？

1. [完成企业认证](https://console.volcengine.com/user/authentication/detail/)

2. [开通语音技术产品](https://console.volcengine.com/speech/app)

3. [创建应用](https://console.volcengine.com/speech/app)，同时勾选大模型语音合成和流式语音识别大模型
    ![alt text](assets/faq1.png)

4. 开通语音合成大模型，确保页面具有音色。注意：语音合成大模型从开通到可以使用有大概5-10分钟延迟
   ![alt text](assets/faq2.png)
   ![alt text](assets/faq3.png)

5. 流式语音识别大模型有试用包，可以不开通。如需提供稳定服务，建议开通正式版本。
   ![alt text](assets/faq4.png)

6. 获取TTS_APP_ID 和TTS_ACCESS_TOKEN
   ![alt text](assets/faq5.png)

7. 获取ASR_APP_ID、ASR_ACCESS_TOKEN，并确认可用的 ASR_RESOURCE_ID（如 `volc.bigasr.sauc.duration`）
   ![alt text](assets/faq6.png)
