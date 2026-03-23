# 系统整体架构

## 架构概览

本项目是一个基于语音交互的实时 AI 面试系统，采用 WebSocket 双向通信，实现候选人与 AI 面试官的自然对话。系统采用双 LLM 架构（Judge + Interviewer），通过结构化面试状态机实现智能追问和流程控制。

### 核心特性

- **实时语音交互**：ASR 语音识别 + TTS 语音合成，低延迟对话体验
- **双 LLM 架构**：Judge LLM 评估回答质量，Interviewer LLM 生成自然对话
- **智能面试流程**：状态机驱动，支持自动追问、题目推进和面试结束
- **并发控制**：准入限制 + 排队机制，保障服务质量
- **性能监控**：Turn Trace 系统记录每轮对话的详细性能指标

## 系统架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                         前端 (React)                              │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │  Check-in 页   │  │  Call 面试页   │  │   Admin 后台     │  │
│  │  设备检测      │  │  实时对话      │  │   管理界面       │  │
│  └────────────────┘  └────────────────┘  └──────────────────┘  │
│         │                    │                      │             │
│         └────────────────────┴──────────────────────┘             │
│                              │                                     │
└──────────────────────────────┼─────────────────────────────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │   Nginx Gateway (80/443)   │
                 │   - 静态资源               │
                 │   - API 代理              │
                 │   - WebSocket 代理        │
                 └─────────────┬─────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
┌───────────────┐   ┌───────────────────┐   ┌────────────────┐
│ WebSocket     │   │ Admin API         │   │ Frontend Log   │
│ Server (8888) │   │ Server (8890)     │   │ Server (8889)  │
│ 面试主链路    │   │ FastAPI           │   │ HTTP 日志收集  │
└───────┬───────┘   └───────────────────┘   └────────────────┘
        │
        │
┌───────┴────────────────────────────────────────────────────────┐
│               后端核心层 (handler.py)                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │ AdmissionController│ │ PersistenceQueue │  │ Logger Cache │ │
│  │ 准入控制 + 排队   │  │ 异步持久化       │  │ 日志管理     │ │
│  └──────────────────┘  └──────────────────┘  └──────────────┘ │
└────────────────────────────────┬───────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │  VoiceBotService        │
                    │  - 双 LLM 协调          │
                    │  - ASR/TTS 管理         │
                    │  - 事件循环编排         │
                    └────────────┬────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
┌───────────────┐      ┌─────────────────┐      ┌──────────────┐
│ InterviewFlow │      │ InterviewJudge  │      │ LLM Limiter  │
│ 面试状态机    │      │ (LLM1)          │      │ 并发控制     │
│ 8状态转换     │      │ 回答评估        │      │ 信号量机制   │
└───────────────┘      └─────────────────┘      └──────────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
                    ▼            ▼            ▼
            ┌──────────┐  ┌──────────┐  ┌──────────┐
            │   ASR    │  │ LLM1/2   │  │   TTS    │
            │ 语音识别 │  │ 大语言模型│  │ 语音合成 │
            └──────────┘  └──────────┘  └──────────┘
                    │            │            │
                    └────────────┴────────────┘
                                 │
                          火山引擎 API
```

## 组件架构

### 前端组件层次

```
App
├── Routes
│   ├── /check-in (设备检测页)
│   │   ├── SpeakerTest (扬声器测试)
│   │   ├── MicrophoneTest (麦克风测试)
│   │   ├── CameraTest (摄像头测试)
│   │   └── ScreenShareTest (屏幕共享测试)
│   │
│   ├── / (面试通话页)
│   │   └── CallInterviewPage
│   │       ├── CallParticipantCard (面试官/候选人视图)
│   │       ├── CallControlBar (控制栏)
│   │       ├── LiveSubtitleBar (实时字幕)
│   │       ├── TranscriptList (对话记录)
│   │       └── DebugDrawer (调试面板)
│   │
│   ├── /hangup-result (面试结束页)
│   │
│   └── /admin (管理后台)
│       ├── /admin/login (登录页)
│       ├── /admin/jobs (岗位管理)
│       └── /admin/interviews (面试记录)
│
├── Providers
│   ├── AuthProvider (认证状态)
│   ├── AudioChatProvider (对话状态)
│   └── AudioChatServiceProvider (服务层)
│       ├── useVoiceBotService (WebSocket 客户端)
│       ├── useAudioRecorder (录音器)
│       └── useSpeakerConfig (音频输出)
```

### 后端服务层次

```
handler.py (服务入口)
├── WebSocket Server (8888)
│   ├── handler(websocket, path)
│   │   ├── token 验证
│   │   ├── 准入控制 (AdmissionController)
│   │   ├── 排队等待 (QueueWaiter)
│   │   └── 创建 VoiceBotService
│   │
│   └── VoiceBotService.handler_loop()
│       ├── 面试模式: _interview_handler_loop()
│       │   ├── 开场白 (scripted TTS)
│       │   ├── 主循环:
│       │   │   ├── handle_input_event (ASR)
│       │   │   ├── InterviewFlow.receive_answer
│       │   │   ├── InterviewJudge.decide (LLM1)
│       │   │   ├── stream_interview_llm_chat (LLM2)
│       │   │   └── handle_tts_response
│       │   └── 结束语 (scripted TTS)
│       │
│       └── 对话模式: 标准流程
│           ├── send_greeting
│           └── 循环: ASR → LLM2 → TTS
│
├── Admin API Server (8890)
│   ├── FastAPI Application
│   ├── /api/admin/* (管理接口)
│   └── /api/health (健康检查)
│
└── Frontend Log Server (8889)
    └── /api/frontend-logs?token=xxx
```

## 双 LLM 架构详解

### LLM1: InterviewJudge (评判器)

**职责**: 评估候选人回答质量，决定是否追问

**输入**:
```python
{
    "question": "请介绍一个你主导的项目",
    "candidate_answer": "我在2023年负责了电商推荐系统...",
    "evidence": {
        "scoring_boundary": "是否包含项目目标、个人动作、可量化结果"
    },
    "follow_up_count": 0
}
```

**输出** (Decision):
```python
{
    "move_forward": False,        # 是否进入下一题
    "need_follow_up": True,       # 是否追问
    "follow_up_question": "能具体说明你的技术选型理由吗？",
    "reason": "回答缺少技术细节",
    "coverage_score": 0.6         # 覆盖度得分 (0~1)
}
```

**配置**:
- Endpoint: `LLM1_ENDPOINT_ID`
- Thinking: `LLM1_THINKING_TYPE` (disabled/enabled)
- Reasoning Effort: `LLM1_REASONING_EFFORT` (minimal/low/medium/high)

### LLM2: Interviewer LLM (对话生成器)

**职责**: 基于 Judge 决策生成自然语言面试官回应

**输入** (通过 `_build_interview_context()` 构建):
```
[评估结果] 评判理由: 回答缺少技术细节
[评估结果] 覆盖度得分: 0.60
[指令] 候选人回答不足，请自然地引导对方补充以下内容。
[追问内容] 能具体说明你的技术选型理由吗？
```

**输出**: 流式文本
```
"听起来是个不错的项目。不过我想了解更多技术细节，
能具体说明一下你当时是如何进行技术选型的吗？"
```

**配置**:
- Endpoint: `LLM2_ENDPOINT_ID`
- System Prompt: `INTERVIEWER_SYSTEM_PROMPT`
- Thinking: `LLM2_THINKING_TYPE`
- Reasoning Effort: `LLM2_REASONING_EFFORT`

### LLM 协作流程

```
候选人回答 "我负责了推荐系统..."
    │
    ▼
┌───────────────────────────────────────┐
│ LLM1 (Judge): 评估回答质量           │
│ - 检查是否覆盖评分标准               │
│ - 决定是否追问                       │
│ - 生成追问内容                       │
└────────────────┬──────────────────────┘
                 │ Decision {
                 │   need_follow_up: true,
                 │   coverage_score: 0.6,
                 │   follow_up_question: "技术选型理由?"
                 │ }
                 ▼
┌───────────────────────────────────────┐
│ InterviewFlow: 状态转换              │
│ DECIDE → ASK_FOLLOWUP → WAIT_ANSWER  │
└────────────────┬──────────────────────┘
                 │
                 ▼
┌───────────────────────────────────────┐
│ LLM2 (Interviewer): 生成自然对话    │
│ - 接收 Judge 评估结果                │
│ - 接收追问内容                       │
│ - 生成流畅的面试官台词               │
└────────────────┬──────────────────────┘
                 │ 流式输出:
                 │ "听起来不错，能详细说说技术选型吗？"
                 ▼
            TTS 语音合成
                 │
                 ▼
            播放给候选人
```

## 技术栈说明

### 前端技术栈

| 类别 | 技术选型 | 说明 |
|-----|---------|------|
| 框架 | React 18 | 函数式组件 + Hooks |
| 路由 | Modern.js Router | 约定式路由 |
| 状态管理 | Context API | 轻量级状态共享 |
| UI 组件 | Ant Design | 企业级 UI 库 |
| 样式 | Tailwind CSS | 原子化 CSS |
| WebSocket | 原生 WebSocket API | 二进制协议支持 |
| 音频处理 | Web Audio API | 音频解码、播放、分析 |
| 录音 | MediaRecorder API | 浏览器原生录音 |
| TypeScript | 5.x | 类型安全 |

### 后端技术栈

| 类别 | 技术选型 | 说明 |
|-----|---------|------|
| 语言 | Python 3.11 | 异步编程支持 |
| 框架 | FastAPI | 管理后台 API |
| WebSocket | websockets | 异步 WebSocket 服务器 |
| 依赖管理 | Poetry | 依赖锁定和虚拟环境 |
| 数据库 | SQLite | 轻量级嵌入式数据库 |
| ORM | SQLAlchemy | 数据库访问层 |
| 日志 | Python logging + AsyncRotatingFileHandler | 异步日志队列 |
| 并发控制 | asyncio.Semaphore | LLM 并发限制 |

### 外部服务依赖

| 服务 | 提供商 | 用途 |
|-----|-------|------|
| LLM (Doubao-Pro-32k) | 火山引擎方舟 | Judge + Interviewer 双 LLM |
| ASR (流式语音识别大模型) | 火山引擎语音技术 | 实时语音识别 (SAUC 协议) |
| TTS (语音合成大模型) | 火山引擎语音技术 | 流式语音合成 |

### 基础设施

| 组件 | 技术 | 说明 |
|-----|------|------|
| 容器化 | Docker + Docker Compose | 服务编排 |
| 反向代理 | Nginx | 静态资源 + API 代理 + WebSocket 升级 |
| SSL 证书 | Let's Encrypt | HTTPS 支持 |
| 部署 | 单机 ECS | Docker Compose 部署 |

## 部署架构

### Docker Compose 架构

```
┌─────────────────────────────────────────────────────┐
│                    宿主机 (ECS)                     │
│                                                     │
│  ┌─────────────────────────────────────────────┐  │
│  │  Docker Network: live_voice_call_default    │  │
│  │                                             │  │
│  │  ┌────────────────────────────────────┐   │  │
│  │  │ gateway (Nginx)                    │   │  │
│  │  │ - 监听: 0.0.0.0:80, 0.0.0.0:443    │   │  │
│  │  │ - 静态资源: /frontend/dist         │   │  │
│  │  │ - API 代理: /api/* -> backend:8890 │   │  │
│  │  │ - 日志代理: /api/frontend-logs ->  │   │  │
│  │  │             backend:8889            │   │  │
│  │  │ - WS 代理: /ws -> backend:8888     │   │  │
│  │  └────────────────┬───────────────────┘   │  │
│  │                   │                        │  │
│  │  ┌────────────────▼───────────────────┐   │  │
│  │  │ backend (Python)                   │   │  │
│  │  │ - WebSocket: 8888 (仅内网)        │   │  │
│  │  │ - Frontend Log: 8889 (仅内网)     │   │  │
│  │  │ - Admin API: 8890 (仅内网)        │   │  │
│  │  │                                    │   │  │
│  │  │ 挂载卷:                            │   │  │
│  │  │ - ./backend/data -> /app/backend/data │  │
│  │  │ - ./backend/logs -> /app/backend/logs │  │
│  │  └────────────────────────────────────┘   │  │
│  └─────────────────────────────────────────────┘  │
│                                                     │
│  ┌─────────────────────────────────────────────┐  │
│  │ 持久化存储 (宿主机目录)                    │  │
│  │ - backend/data/storage/                     │  │
│  │   ├── interviews.db (SQLite)               │  │
│  │   ├── interview_logs/<token>/              │  │
│  │   │   ├── backend.log                      │  │
│  │   │   └── frontend.log                     │  │
│  │   └── interview_audio/<token>/             │  │
│  │       ├── candidate.wav                    │  │
│  │       └── interviewer.mp3                  │  │
│  │                                             │  │
│  │ - backend/logs/                             │  │
│  │   └── backend-YYYYmmdd-HHMMSS-p<PID>.log   │  │
│  └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 网络流向

**外部访问** → **Nginx Gateway (80/443)** → **Backend Services (内网)**

**端口映射**:
- `80 (HTTP)` → gateway
- `443 (HTTPS)` → gateway
- 内网端口 `8888/8889/8890` 不对外暴露

### 安全策略

1. **端口隔离**: Backend 服务仅在容器网络内可访问
2. **HTTPS 强制**: Nginx 配置 HTTP 自动跳转 HTTPS
3. **Token 认证**: 面试链接必须携带有效 token
4. **CORS 限制**: Admin API 配置白名单域名
5. **SSL 证书**: Let's Encrypt 自动签发和续订

## 关键配置

### 环境变量 (.env)

```bash
# === 核心配置 (必填) ===
ARK_API_KEY=<火山方舟 API 密钥>
LLM1_ENDPOINT_ID=<Judge LLM 端点>
LLM2_ENDPOINT_ID=<Interviewer LLM 端点>
ASR_APP_ID=<ASR 应用 ID>
ASR_ACCESS_TOKEN=<ASR 访问令牌>
ASR_RESOURCE_ID=volc.bigasr.sauc.duration
TTS_APP_ID=<TTS 应用 ID>
TTS_ACCESS_TOKEN=<TTS 访问令牌>
TTS_SPEAKER=<发音人 ID>

# === 深度思考配置 (可选) ===
LLM1_THINKING_TYPE=disabled
LLM2_THINKING_TYPE=disabled
LLM1_REASONING_EFFORT=minimal
LLM2_REASONING_EFFORT=minimal

# === 并发控制 ===
MAX_ACTIVE_INTERVIEWS=5
QUEUE_WAIT_TIMEOUT_SECONDS=1800
LLM_CONCURRENT_REQUESTS=5

# === 管理后台 ===
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123456
PUBLIC_INTERVIEW_BASE_URL=https://smartinterview.cn/check-in

# === HTTPS 部署 ===
LETSENCRYPT_EMAIL=your-email@example.com
```

### Docker Compose 服务定义

```yaml
version: "3.8"

services:
  backend:
    build: ./backend
    container_name: live_voice_call_backend
    env_file: .env
    volumes:
      - ./backend/data:/app/backend/data
      - ./backend/logs:/app/backend/logs
    networks:
      - default

  gateway:
    image: nginx:alpine
    container_name: live_voice_call_gateway
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./frontend/dist:/usr/share/nginx/html
      - ./deploy/nginx.conf:/etc/nginx/conf.d/default.conf
      - ./deploy/letsencrypt:/etc/letsencrypt:ro
    depends_on:
      - backend
    networks:
      - default

networks:
  default:
    name: live_voice_call_default
```

## 性能指标

### 延迟指标

| 指标 | 目标值 | 说明 |
|-----|-------|------|
| ASR 静默检测 | 2000ms | 检测候选人说话结束 |
| Judge LLM 响应 | < 2000ms | LLM1 评估延迟 |
| Interviewer LLM TTFT | < 1000ms | LLM2 首 token 延迟 |
| 识别到首句播放 | < 5000ms | 用户感知延迟 (rec_to_first_sentence_ms) |
| 完整轮次延迟 | < 10000ms | 识别到回复播放完毕 (rec_to_tts_done_ms) |

### 并发能力

| 配置 | 默认值 | 说明 |
|-----|-------|------|
| 最大并发面试数 | 5 | `MAX_ACTIVE_INTERVIEWS` |
| LLM 并发请求数 | 5 | `LLM_CONCURRENT_REQUESTS` |
| 排队超时 | 1800s (30分钟) | `QUEUE_WAIT_TIMEOUT_SECONDS` |
| 持久化队列大小 | 200 | `PERSISTENCE_QUEUE_SIZE` |

### 资源占用 (单场面试)

| 资源 | 预估值 | 说明 |
|-----|-------|------|
| 内存 | ~50MB | VoiceBotService + ASR/TTS 客户端 |
| 音频累积 | ~2MB | 10分钟面试音频 |
| 日志大小 | ~500KB | 完整对话 + 性能指标 |
| 数据库记录 | ~10KB | 对话轮次 + 元数据 |

## 扩展性考虑

### 水平扩展

**当前架构限制**:
- SQLite 不支持多实例并发写入
- 本地文件系统存储音频和日志

**扩展方案**:
1. 数据库迁移到 PostgreSQL/MySQL
2. 对象存储 (OSS) 存储音频文件
3. 分布式日志收集 (ELK/Loki)
4. 负载均衡器 (Nginx/HAProxy) 分发 WebSocket

### 垂直扩展

**优化建议**:
1. 增加 `MAX_ACTIVE_INTERVIEWS` (需评估 LLM 服务端承载能力)
2. 增加 `LLM_CONCURRENT_REQUESTS` (建议 >= 2 * MAX_ACTIVE_INTERVIEWS)
3. 启用 Redis 缓存 Judge 决策结果
4. 使用 Gunicorn + Uvicorn 多进程部署 Admin API

## 相关文档

- [数据流向详解](DATA_FLOW.md)
- [性能优化指南](PERFORMANCE.md)
- [故障排查手册](TROUBLESHOOTING.md)
- [模块详细文档](modules/)
- [部署指南](../deploy/DEPLOY.md)
