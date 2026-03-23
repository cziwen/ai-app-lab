# 完整数据流向图

## 概述

本文档详细描述语音面试系统中数据的完整流向，包括面试流程、WebSocket 通信、音频流处理和状态转换的全链路追踪。

## 面试完整流程数据流

### 阶段 1：准入与排队

```
候选人打开链接 (http://domain/check-in?token=abc123)
    │
    ▼
前端解析 token 并建立 WebSocket 连接
    │ ws://backend:8888?token=abc123
    │
    ▼
┌────────────────────────────────────────┐
│ handler.py: handler(websocket, path)  │
│ 1. 解析 token                         │
│ 2. 验证 token 有效性                  │
│    ├─ 无效 → BotError "INVALID_TOKEN" │
│    └─ 有效 → 继续                     │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ AdmissionController.acquire_or_enqueue │
│ 检查并发限制 (MAX_ACTIVE_INTERVIEWS)  │
└────────────────┬───────────────────────┘
                 │
         ┌───────┴───────┐
         │               │
    立即准入         进入排队
         │               │
         ▼               ▼
  跳到阶段2      ┌──────────────────┐
                 │ 排队循环         │
                 │ 每5秒发送:       │
                 │ - QueueUpdate    │
                 │   {position: N}  │
                 │                  │
                 │ 等待准入事件:    │
                 │ waiter.admitted  │
                 │ _event.wait()    │
                 │                  │
                 │ 准入成功:        │
                 │ - QueueAdmitted  │
                 └────────┬─────────┘
                          │
                          └─────────► 跳到阶段2
```

### 阶段 2：面试初始化

```
┌────────────────────────────────────────┐
│ 创建 VoiceBotService 实例             │
│ - 初始化 ASR 客户端                   │
│ - 初始化 TTS 客户端                   │
│ - 创建 InterviewFlow                  │
│ - 创建 InterviewJudge                 │
│ - 注册回调函数:                       │
│   ├─ on_candidate_sentence            │
│   ├─ on_bot_sentence                  │
│   ├─ on_bot_audio_chunk               │
│   └─ on_interview_completed           │
└────────────────┬───────────────────────┘
                 │
                 ▼
    await service.init()
                 │
                 ▼
┌────────────────────────────────────────┐
│ 开场白流程 (无 LLM 调用)             │
│                                        │
│ 1. flow.produce_interviewer_message() │
│    INTRO → ASK_QUESTION               │
│    ├─ interviewer_text = "欢迎..."   │
│    └─ 记录到 history_messages         │
│                                        │
│ 2. _send_scripted_text()              │
│    ├─ TTS 合成音频                    │
│    ├─ 下发: TTSSentenceStart          │
│    ├─ 下发: TTSSentenceEnd (音频)     │
│    └─ 下发: TTSDone                   │
│                                        │
│ 3. flow.produce_interviewer_message() │
│    ASK_QUESTION → WAIT_ANSWER         │
│    ├─ interviewer_text = "第一题..."  │
│    └─ TTS 播放                        │
└────────────────┬───────────────────────┘
                 │
                 ▼
         进入主循环 (阶段3)
```

### 阶段 3：对话主循环 (双 LLM)

```
┌──────────────────────────────────────────────────────────┐
│                    候选人回答阶段                        │
└──────────────────────────────────────────────────────────┘
    │
    ▼
前端: 用户按下麦克风并说话
    │
    ▼
┌────────────────────────────────────────┐
│ 前端: MediaRecorder 录制音频          │
│ - 格式: PCM 16位单声道 16kHz          │
│ - 每帧: 20-100ms (320-1600 字节)      │
└────────────────┬───────────────────────┘
                 │
                 ▼ 每帧发送 UserAudio 事件
         WebSocket.send(pack({
             event: "UserAudio",
             data: <audio_bytes>
         }))
                 │
                 ▼
┌────────────────────────────────────────┐
│ 后端: handle_input_event()            │
│ 1. 检查服务状态 (必须 Idle)          │
│ 2. 确保 ASR 客户端就绪                │
│ 3. 提取音频数据并累积                │
│    candidate_audio.extend(pcm_bytes)  │
│ 4. 流式传给 ASR 客户端                │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ ASR 识别流程 (SAUC 协议)             │
│                                        │
│ asr_client.stream_asr(audio_stream)   │
│   ├─ 实时识别中间结果                │
│   │  (累积到 asr_buffer)              │
│   │                                   │
│   └─ 静默检测 (2000ms 无新文本)       │
│      ├─ 触发 finalize                 │
│      ├─ 返回完整句子                  │
│      └─ 关闭 ASR 流                   │
└────────────────┬───────────────────────┘
                 │
                 ▼
     SentenceRecognizedPayload {
         sentence: "我在2023年负责了推荐系统..."
     }
                 │
                 ▼
┌────────────────────────────────────────┐
│ 下发给前端: SENTENCE_RECOGNIZED        │
│ 前端显示候选人说话内容                │
└────────────────┬───────────────────────┘
                 │
                 ▼
        服务状态: Idle → InProgress
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│                    双 LLM 评估与生成阶段                 │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌────────────────────────────────────────┐
│ InterviewFlow.receive_candidate_answer │
│ (WAIT_ANSWER → EVAL_ANSWER → DECIDE)  │
│                                        │
│ 1. 记录回答到 QuestionContext.turns   │
│ 2. 增加全局轮次计数                   │
│ 3. 检查全局轮次限制                   │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ LLM1 (Judge) 评估                     │
│                                        │
│ async with llm_slot():                 │
│   decision = judge.decide(             │
│     question="请介绍项目",             │
│     answer="我负责了推荐系统...",      │
│     evidence={scoring_boundary},       │
│     follow_up_count=0                  │
│   )                                    │
│                                        │
│ Judge Prompt:                          │
│ ┌──────────────────────────────────┐  │
│ │你是面试评判专家，评估回答质量    │  │
│ │- 问题: ...                        │  │
│ │- 回答: ...                        │  │
│ │- 评分标准: ...                    │  │
│ │- 已追问次数: 0 (最多2次)         │  │
│ │                                   │  │
│ │输出 JSON:                         │  │
│ │{                                  │  │
│ │  "coverage_score": 0.6,           │  │
│ │  "need_follow_up": true,          │  │
│ │  "follow_up_question": "技术选型?"│  │
│ │}                                  │  │
│ └──────────────────────────────────┘  │
└────────────────┬───────────────────────┘
                 │
                 ▼
     Decision {
         move_forward: false,
         need_follow_up: true,
         follow_up_question: "能具体说明技术选型理由吗？",
         reason: "回答缺少技术细节",
         coverage_score: 0.6
     }
                 │
                 ▼
┌────────────────────────────────────────┐
│ InterviewFlow 状态转换                │
│ DECIDE → ASK_FOLLOWUP                 │
│ (因为 need_follow_up=true)            │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ flow.produce_interviewer_message()    │
│ ASK_FOLLOWUP → WAIT_ANSWER            │
│ - interviewer_text = "技术选型理由?"   │
│   (原始追问内容，待 LLM2 润色)        │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ LLM2 (Interviewer) 生成自然对话      │
│                                        │
│ interview_context = _build_context(    │
│   decision, next_question, flow.state  │
│ )                                      │
│                                        │
│ Context 示例:                          │
│ ┌──────────────────────────────────┐  │
│ │[评估结果] 评判理由: 缺少技术细节  │  │
│ │[评估结果] 覆盖度得分: 0.60        │  │
│ │[指令] 候选人回答不足，请自然引导  │  │
│ │[追问内容] 技术选型理由?           │  │
│ └──────────────────────────────────┘  │
│                                        │
│ async with llm_slot():                 │
│   async for delta in adapter.stream(  │
│     model=llm2_endpoint_id,            │
│     system=INTERVIEWER_SYSTEM_PROMPT,  │
│     messages=[interview_context]       │
│   ):                                   │
│     yield delta  # 流式输出           │
└────────────────┬───────────────────────┘
                 │
                 ▼
    流式文本生成: "听起来是个不错的项目。
                   不过我想了解更多技术细节，
                   能具体说明一下技术选型理由吗？"
                 │
                 ▼
┌────────────────────────────────────────┐
│ handle_tts_response()                 │
│ 流式调用 TTS 合成音频                 │
│                                        │
│ async for tts_rsp in tts_client.tts(  │
│   source=llm_output_stream             │
│ ):                                     │
│   if tts_rsp.event == SentenceStart:  │
│     ├─ 下发: TTSSentenceStart          │
│     │  {sentence: "听起来不错..."}     │
│     └─ 前端显示面试官文本              │
│                                        │
│   if tts_rsp.audio:                    │
│     ├─ 累积音频块                      │
│     ├─ 下发: TTSSentenceEnd (音频)     │
│     ├─ 触发回调: on_bot_audio_chunk    │
│     └─ 前端播放音频                    │
│                                        │
│   if tts_rsp.event == SessionFinished: │
│     ├─ 下发: TTSDone                   │
│     └─ 前端允许用户说话                │
└────────────────┬───────────────────────┘
                 │
                 ▼
        服务状态: InProgress → Idle
                 │
                 ▼
        回到候选人回答阶段，循环继续
```

### 阶段 4：面试结束

```
┌────────────────────────────────────────┐
│ 触发结束条件 (任一)                   │
│ 1. 题库耗尽 (所有问题问完)            │
│ 2. 全局轮次限制 (候选人发言20次)      │
│ 3. 最后一题评估完成且 move_forward     │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ flow.is_done = True                   │
│ flow.state = WRAP_UP                  │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ flow.produce_interviewer_message()    │
│ WRAP_UP → DONE                        │
│ - interviewer_text = "感谢参加..."    │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ _send_scripted_text(wrap_text)        │
│ - TTS 播放结束语                      │
│ - 下发: TTSSentenceStart              │
│ - 下发: TTSSentenceEnd (音频)         │
│ - 下发: TTSDone                       │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ 触发回调: on_interview_completed()    │
│ 标记面试状态为 "completed"            │
└────────────────┬───────────────────────┘
                 │
                 ▼
        退出事件循环
                 │
                 ▼
┌────────────────────────────────────────┐
│ finally 清理块                        │
│ 1. 释放准入控制:                      │
│    await ADMISSION.release(token)      │
│                                        │
│ 2. 提交持久化任务:                    │
│    await PERSISTENCE.submit(           │
│      PersistenceTask(                  │
│        token=token,                    │
│        turns=history,                  │
│        candidate_pcm_bytes=audio,      │
│        interviewer_encoded_bytes=mp3,  │
│        interview_completed=True        │
│      )                                 │
│    )                                   │
│                                        │
│ 3. 释放日志 Logger:                   │
│    _release_interview_loggers(token)   │
└────────────────┬───────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────┐
│ 持久化队列异步处理                    │
│ (在后台 worker 中执行)                │
│                                        │
│ 1. save_interview_turns()             │
│    - 保存对话轮次到数据库              │
│                                        │
│ 2. persist_interview_audio()          │
│    - 保存候选人音频: candidate.wav     │
│    - 保存面试官音频: interviewer.mp3   │
│                                        │
│ 3. mark_interview_completed()         │
│    - 更新 interview 状态               │
│    - 计算面试时长                      │
│                                        │
│ (失败自动重试，指数退避: 0.3s→0.6s→1.2s) │
└────────────────┬───────────────────────┘
                 │
                 ▼
          WebSocket 连接关闭
                 │
                 ▼
      前端跳转到 /hangup-result 页面
```

## WebSocket 通信协议流程

### 协议结构

**消息格式**: Header (8 字节) + Payload (可变长度)

```
┌────────────────────────────────────────┐
│ Byte 0: [协议版本(4bit) | Header大小(4bit)] │
│ Byte 1: [消息类型(4bit) | 序列标志(4bit)]    │
│ Byte 2: [序列化方式(4bit) | 压缩方式(4bit)] │
│ Byte 3: [保留位(8bit)]                       │
│ Byte 4-7: [Payload 长度 (32bit 大端序)]     │
│ Byte 8+: [Payload 数据]                      │
└────────────────────────────────────────┘
```

### 上行消息 (前端 → 后端)

#### 1. 完整请求 (CLIENT_FULL_REQUEST = 0b0001)

```
前端代码:
    websocket.send(pack({
        event: "BotUpdateConfig",
        payload: { speaker: "zh_female_tianmei" }
    }))

编码后:
    Header: [0x11, 0x10, 0x10, 0x00, 0x00, 0x00, 0x00, 0x3A]
            │     │     │     │     └─────────────┘
            │     │     │     │     Payload 长度: 58 字节
            │     │     │     保留位
            │     │     JSON 序列化 + 无压缩
            │     完整请求 + 无序列号
            协议版本 v1 + Header 4字节

    Payload (JSON):
    {
        "event": "BotUpdateConfig",
        "payload": {
            "speaker": "zh_female_tianmei"
        }
    }
```

#### 2. 音频请求 (CLIENT_AUDIO_ONLY_REQUEST = 0b0010)

```
前端代码:
    websocket.send(pack({
        event: "UserAudio",
        data: <Blob of PCM audio>
    }))

编码后:
    Header: [0x11, 0x20, 0x00, 0x00, 0x00, 0x00, 0x01, 0x40]
            │     │     │     │     └─────────────┘
            │     │     │     │     Payload 长度: 320 字节 (20ms 音频)
            │     │     │     保留位
            │     │     无序列化 + 无压缩
            │     音频请求 + 无序列号
            协议版本 v1 + Header 4字节

    Payload (二进制):
    [0x00, 0x12, 0xFF, 0x34, ...] (PCM 音频数据)
```

### 下行消息 (后端 → 前端)

#### 1. 完整响应 (SERVER_FULL_RESPONSE = 0b1001)

```
后端代码:
    yield WebEvent.from_payload(
        SentenceRecognizedPayload(
            sentence="我在2023年负责了推荐系统"
        )
    )

编码后:
    Header: [0x11, 0x90, 0x10, 0x00, 0x00, 0x00, 0x00, 0x5E]
            │     │     │     │     └─────────────┘
            │     │     │     │     Payload 长度: 94 字节
            │     │     │     保留位
            │     │     JSON 序列化 + 无压缩
            │     服务端响应 + 无序列号
            协议版本 v1 + Header 4字节

    Payload (JSON):
    {
        "event": "SentenceRecognized",
        "payload": {
            "sentence": "我在2023年负责了推荐系统"
        }
    }
```

#### 2. 音频响应 (SERVER_AUDIO_ONLY_RESPONSE = 0b1011)

```
后端代码:
    yield WebEvent.from_payload(
        TTSSentenceEndPayload(data=mp3_bytes)
    )

编码后:
    Header: [0x11, 0xB0, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00]
            │     │     │     │     └─────────────┘
            │     │     │     │     Payload 长度: 4096 字节 (MP3 音频)
            │     │     │     保留位
            │     │     无序列化 + 无压缩
            │     音频响应 + 无序列号
            协议版本 v1 + Header 4字节

    Payload (二进制):
    [0xFF, 0xFB, 0x90, ...] (MP3 音频数据)
```

### 完整对话的消息序列

```
Time  Direction  Event                  Payload
────────────────────────────────────────────────────────────
0ms   ← Backend  BotReady               {session: "uuid-1"}

1000ms → Frontend UserAudio             <audio_chunk_1>
1020ms → Frontend UserAudio             <audio_chunk_2>
...
2500ms → Frontend UserAudio             <audio_chunk_50>

(ASR 检测到静默)
3000ms ← Backend  SentenceRecognized    {sentence: "我想介绍..."}

(LLM1 Judge 评估中...)
4500ms (Judge 完成, LLM2 开始生成)

5000ms ← Backend  TTSSentenceStart      {sentence: "听起来不错..."}
5100ms ← Backend  AudioOnly             <mp3_chunk_1>
5200ms ← Backend  AudioOnly             <mp3_chunk_2>
...
8000ms ← Backend  TTSSentenceEnd        <last_mp3_chunk>
8100ms ← Backend  TTSDone               {}

(用户继续回答)
9000ms → Frontend UserAudio             <audio_chunk_51>
...
```

## 音频流处理

### 候选人音频处理链

```
┌──────────────────────────────────────────────────────────┐
│ 前端: 浏览器麦克风输入                                   │
│ MediaDevices.getUserMedia({audio: true})                 │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ MediaRecorder API                                        │
│ - mimeType: audio/webm                                   │
│ - audioBitsPerSecond: 128000                             │
│ - timeslice: 100ms                                       │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼ ondataavailable 事件
┌──────────────────────────────────────────────────────────┐
│ WebM → PCM 转换 (Web Audio API)                         │
│ 1. AudioContext.decodeAudioData(webm_blob)              │
│ 2. 提取 Float32Array 音频数据                           │
│ 3. 转换为 Int16Array (16位 PCM)                         │
│ 4. 格式: 单声道, 16kHz 采样率                           │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
     每 100ms 生成 320 字节 PCM 数据
     (16kHz * 2字节/采样 * 0.1秒 = 3200 字节)
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ WebSocket 发送                                           │
│ pack({                                                   │
│   event: "UserAudio",                                    │
│   data: new Blob([pcm_int16_array])                      │
│ })                                                       │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ 后端: handle_input_event()                              │
│ 1. 提取音频数据: _extract_pcm_audio()                   │
│    - 拒绝旧协议嵌套帧                                   │
│    - 验证 PCM 格式 (长度必须是偶数)                     │
│ 2. 累积音频: candidate_audio.extend(pcm_bytes)          │
│ 3. 转发给 ASR: asr_client.stream_asr()                  │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ ASR Client (SAUC 协议)                                  │
│ WebSocket 连接到 openspeech.bytedance.com               │
│ 1. 发送 Start Frame:                                    │
│    {                                                     │
│      format: {                                           │
│        encoding: "pcm",                                  │
│        sample_rate: 16000,                               │
│        bits_per_sample: 16                               │
│      },                                                  │
│      resource_id: "volc.bigasr.sauc.duration"           │
│    }                                                     │
│ 2. 流式发送 Audio Frame:                                │
│    [sequence_id, audio_data]                             │
│ 3. 接收识别结果:                                        │
│    - is_final=false: 中间结果 (不断更新)                │
│    - is_final=true: 最终结果 (静默检测后)               │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ handle_asr_response()                                   │
│ 静默检测逻辑:                                           │
│ - 每 200ms 检查一次                                     │
│ - 如果 asr_buffer 超过 2000ms 未增长:                   │
│   ├─ 触发 finalize                                      │
│   ├─ 返回 SentenceRecognizedPayload                     │
│   └─ 关闭 ASR 流                                        │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
     识别文本: "我在2023年负责了推荐系统..."
                 │
                 ▼
         传递给 InterviewFlow 处理
```

### 面试官音频处理链

```
┌──────────────────────────────────────────────────────────┐
│ LLM2 (Interviewer) 流式生成文本                         │
│ async for delta in adapter.stream_text(...):            │
│   yield delta  # "听", "起", "来", "不", "错", ...      │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ handle_tts_response()                                   │
│ 流式调用 TTS:                                           │
│ async for tts_rsp in tts_client.tts(                    │
│   source=llm_output_stream,                              │
│   speaker=service.tts_speaker                            │
│ ):                                                       │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ TTS Client (火山引擎)                                   │
│ WebSocket 连接到 openspeech.bytedance.com               │
│ 1. 发送配置:                                            │
│    {                                                     │
│      app: {appid, token},                                │
│      user: {uid},                                        │
│      audio: {                                            │
│        voice_type: "zh_female_tianmei",                  │
│        encoding: "mp3",                                  │
│        speed_ratio: 1.0,                                 │
│        volume_ratio: 1.0                                 │
│      }                                                   │
│    }                                                     │
│ 2. 流式发送文本:                                        │
│    {                                                     │
│      payload_type: 1,  # 文本数据                       │
│      payload: "听起来不错"                               │
│    }                                                     │
│ 3. 接收音频:                                            │
│    {                                                     │
│      event: "SentenceStart",                             │
│      payload: {text: "听起来不错"}                       │
│    }                                                     │
│    {                                                     │
│      event: "Audio",                                     │
│      payload: <mp3_bytes>                                │
│    }                                                     │
│    {                                                     │
│      event: "SentenceEnd"                                │
│    }                                                     │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────┐
│ Service 层处理                                          │
│ if tts_rsp.event == EventTTSSentenceStart:              │
│   ├─ 下发: TTSSentenceStart {sentence: text}            │
│   └─ 触发回调: on_bot_sentence(text)                    │
│                                                          │
│ if tts_rsp.audio:                                        │
│   ├─ 累积: buffer.extend(tts_rsp.audio)                 │
│   └─ 触发回调: on_bot_audio_chunk(audio)                │
│                                                          │
│ if tts_rsp.event == EventTTSSentenceEnd:                │
│   ├─ 下发: TTSSentenceEnd {data: buffer}                │
│   └─ buffer.clear()                                     │
│                                                          │
│ if tts_rsp.event == EventSessionFinished:               │
│   ├─ 下发: TTSDone {}                                   │
│   └─ 关闭 TTS 客户端                                    │
└────────────────┬─────────────────────────────────────────┘
                 │
                 ▼ WebSocket 发送给前端
┌──────────────────────────────────────────────────────────┐
│ 前端: VoiceBotService                                   │
│ 1. 接收 SERVER_AUDIO_ONLY_RESPONSE:                     │
│    ├─ 解码 Header 提取音频数据                          │
│    ├─ 入队: audioChunks.push(data)                      │
│    └─ 启动播放                                          │
│                                                          │
│ 2. 音频路由选择:                                        │
│    ├─ iOS Safari: media-element 模式                    │
│    │   ├─ 创建 Audio 元素                                │
│    │   ├─ src = URL.createObjectURL(mp3_blob)           │
│    │   └─ audio.play()                                  │
│    │                                                     │
│    └─ 其他浏览器: web-audio-fallback 模式                │
│        ├─ AudioContext.decodeAudioData(mp3_bytes)       │
│        ├─ 创建 AudioBufferSourceNode                     │
│        ├─ 连接 AnalyserNode (音量分析)                  │
│        └─ source.start()                                 │
│                                                          │
│ 3. 实时音量分析 (web-audio):                            │
│    ├─ analyser.getByteTimeDomainData()                  │
│    ├─ 计算 RMS: sqrt(sum(v^2) / N)                      │
│    └─ 触发回调: onAudioLevelChange(normalizedLevel)     │
│                                                          │
│ 4. 播放完成:                                            │
│    ├─ 清理资源: URL.revokeObjectURL()                   │
│    ├─ 触发回调: onStopPlayAudio()                       │
│    └─ playNextAudioChunk()  # 播放队列下一帧            │
└──────────────────────────────────────────────────────────┘
```

## 状态转换流程

### InterviewFlow 状态机

```
┌────────────────────────────────────────────────────────┐
│ 状态机定义 (8 个状态)                                 │
│                                                        │
│ INTRO          → 开场白                               │
│ ASK_QUESTION   → 提出主问题                           │
│ WAIT_ANSWER    → 等待候选人回答                       │
│ EVAL_ANSWER    → 评估回答 (瞬态)                      │
│ DECIDE         → 决策下一步 (瞬态)                    │
│ ASK_FOLLOWUP   → 提出追问                             │
│ WRAP_UP        → 结束语                               │
│ DONE           → 面试完成 (终态)                      │
└────────────────────────────────────────────────────────┘

完整流程:

INTRO
  │ produce_interviewer_message()
  ├─ TTS: "欢迎参加面试，本场共3道题..."
  └─ state = ASK_QUESTION

ASK_QUESTION
  │ produce_interviewer_message()
  ├─ TTS: "第一题，请用1分钟做自我介绍..."
  └─ state = WAIT_ANSWER

WAIT_ANSWER
  │ 候选人说话...
  │ receive_candidate_answer("我叫张三...")
  ├─ state = EVAL_ANSWER (瞬态)
  ├─ 调用 Judge LLM
  └─ state = DECIDE (瞬态)

DECIDE
  │ 根据 Decision 决定下一步:
  │
  ├─ 情况1: need_follow_up=True && follow_up_count < 2
  │   └─ state = ASK_FOLLOWUP
  │
  ├─ 情况2: move_forward=True && 有下一题
  │   └─ state = ASK_QUESTION
  │
  └─ 情况3: move_forward=True && 题库耗尽
      └─ state = WRAP_UP

ASK_FOLLOWUP
  │ produce_interviewer_message()
  ├─ TTS: "能详细说明你的项目经验吗？"
  └─ state = WAIT_ANSWER
      │
      └─ 回到候选人回答阶段

WRAP_UP
  │ produce_interviewer_message()
  ├─ TTS: "感谢参加本次面试，我们会尽快给您反馈"
  └─ state = DONE

DONE
  │ 终态，流程结束
  └─ on_interview_completed() 触发
```

### 状态转换时序图

```
Time    State           Action                          LLM Call
──────────────────────────────────────────────────────────────────
0s      INTRO           send_greeting                   None
1s      ASK_QUESTION    ask_first_question              None
5s      WAIT_ANSWER     (等待候选人)                    None

10s     (候选人开始说话)
20s     WAIT_ANSWER     receive_answer("我叫...")       None
21s     EVAL_ANSWER     (瞬态)                          LLM1 (Judge)
23s     DECIDE          (瞬态，决策: 追问)              None
24s     ASK_FOLLOWUP    ask_follow_up                   LLM2 (Interviewer)
28s     WAIT_ANSWER     (等待候选人)                    None

35s     (候选人继续回答)
50s     WAIT_ANSWER     receive_answer("我负责...")     None
51s     EVAL_ANSWER     (瞬态)                          LLM1 (Judge)
53s     DECIDE          (瞬态，决策: 下一题)            None
54s     ASK_QUESTION    ask_next_question               LLM2 (Interviewer)
58s     WAIT_ANSWER     (等待候选人)                    None

...     (继续循环)

180s    DECIDE          (最后一题完成)                  None
181s    WRAP_UP         send_closing                    None
185s    DONE            (面试结束)                      None
```

### 强制转换条件

```
┌────────────────────────────────────────────────────────┐
│ Guard 1: 全局轮次限制                                 │
│ if total_candidate_turns >= global_turn_limit (20):   │
│   forced_decision = Decision(                          │
│     move_forward=True,                                 │
│     need_follow_up=False,                              │
│     reason="global_turn_limit_reached"                 │
│   )                                                    │
│   state = WRAP_UP                                     │
│   return                                               │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│ Guard 2: 单题追问上限                                 │
│ if follow_up_count >= max_followups_per_question (2):  │
│   # Judge 强制返回:                                   │
│   decision.need_follow_up = False                      │
│   decision.move_forward = True                         │
│   reason += " (reached_max_followups)"                 │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│ Guard 3: 题库耗尽                                     │
│ if current_question_index >= len(questions) - 1:       │
│   state = WRAP_UP                                     │
│   reason = "all_questions_completed"                   │
└────────────────────────────────────────────────────────┘
```

## 性能监控数据流

### Turn Trace 系统

```
每个对话轮次开始时:
┌────────────────────────────────────────┐
│ _start_turn()                         │
│ - turn_id = uuid.uuid4()              │
│ - turn_timestamps_ms = {}             │
└────────────────┬───────────────────────┘
                 │
                 ▼
记录关键时间戳:

1. turn_recognized_emitted
   ↓ (ASR 识别完成)

2. judge_start
   ↓ (Judge LLM 开始)

3. judge_end
   ↓ (Judge LLM 结束)

4. interviewer_llm_start
   ↓ (Interviewer LLM 开始)

5. interviewer_llm_first_token
   ↓ (LLM2 首 token)

6. interviewer_llm_end
   ↓ (LLM2 完成)

7. tts_init_start
   ↓ (TTS 初始化)

8. tts_init_end
   ↓ (TTS 就绪)

9. tts_stream_start
   ↓ (TTS 流开始)

10. tts_first_sentence_start
    ↓ (首句播放)

11. tts_done
    ↓ (TTS 完成)

轮次结束时:
┌────────────────────────────────────────┐
│ _end_turn()                           │
│ _log_turn_latency_breakdown()        │
│                                        │
│ 计算性能指标:                         │
│ - judge_ms                             │
│   = judge_end - judge_start            │
│                                        │
│ - llm2_ttft_ms                         │
│   = first_token - interviewer_start    │
│                                        │
│ - llm2_total_ms                        │
│   = interviewer_end - interviewer_start│
│                                        │
│ - tts_init_ms                          │
│   = tts_init_end - tts_init_start      │
│                                        │
│ - rec_to_first_sentence_ms             │
│   = tts_first_sentence - recognized    │
│   (用户感知延迟)                      │
│                                        │
│ - rec_to_tts_done_ms                   │
│   = tts_done - recognized              │
│   (总延迟)                            │
└────────────────┬───────────────────────┘
                 │
                 ▼
写入面试日志:
[TurnTrace] turn_id=xxx judge_ms=1800 llm2_ttft_ms=800
            rec_to_first_sentence_ms=4500
            rec_to_tts_done_ms=9200 status=success
```

## 相关文档

- [系统架构](ARCHITECTURE.md)
- [性能优化指南](PERFORMANCE.md)
- [故障排查手册](TROUBLESHOOTING.md)
