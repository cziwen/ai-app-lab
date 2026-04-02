# backend/event.py

## 模块概述

定义 WebSocket 通信协议的事件类型和 Payload 数据结构，用于前后端双向通信。

## 事件类型常量

```python
# 后端 → 前端
BOT_READY = "BotReady"                    # 机器人就绪
SENTENCE_RECOGNIZED = "SentenceRecognized"  # 语音识别完成
TTS_SENTENCE_START = "TTSSentenceStart"   # TTS句子开始
TTS_SENTENCE_END = "TTSSentenceEnd"       # TTS句子结束（含音频数据）
TTS_DONE = "TTSDone"                      # TTS完成
BOT_ERROR = "BotError"                    # 错误事件
QUEUE_ENTERED = "QueueEntered"            # 进入排队
QUEUE_UPDATE = "QueueUpdate"              # 排队位置更新
QUEUE_ADMITTED = "QueueAdmitted"          # 准入成功
QUEUE_TIMEOUT = "QueueTimeout"            # 排队超时
QUEUE_CANCELLED = "QueueCancelled"        # 排队取消

# 前端 → 后端
USER_AUDIO = "UserAudio"                  # 用户音频帧
BOT_UPDATE_CONFIG = "BotUpdateConfig"     # 更新配置（如切换发音人）
```

## 核心类

### WebEvent

```python
class WebEvent(BaseModel):
    event: str                    # 事件类型
    payload: Optional[WebPayload]  # Payload对象
    data: Optional[bytes]          # 二进制数据（如音频）
```

### 内部 ASR 响应对象（非 WebSocket 协议）

`SaucASRFullServerResponse` 是后端内部对象，不直接下发给前端。该对象新增了内部字段：

```python
class SaucASRFullServerResponse:
    result: Optional[SaucASRResult]
    audio: Optional[SaucASRAudio]
    payload: Dict[str, Any]
    stream_connect_id: str = ""  # ASR 连接代次标识
```

用途：在 `service.handle_asr_response()` 中做跨题尾包隔离（旧连接包丢弃）。
注意：这不是 `WebEvent` 字段，不影响前端 `event/payload/data` 协议。

### Payload 类型

#### 1. BotReadyPayload
```python
class BotReadyPayload(WebPayload, BaseModel):
    session: str  # 会话ID（UUID）
```

**触发时机**：Service初始化完成后
**前端处理**：显示"准备就绪"，启用麦克风

#### 2. SentenceRecognizedPayload
```python
class SentenceRecognizedPayload(WebPayload, BaseModel):
    sentence: str  # 识别文本
```

**触发时机**：ASR检测到静音并完成识别
**前端处理**：显示候选人说话内容

#### 3. TTSSentenceStartPayload
```python
class TTSSentenceStartPayload(WebPayload, BaseModel):
    sentence: str  # 即将播放的句子文本
```

**触发时机**：TTS开始合成一个句子
**前端处理**：显示机器人说话内容（文字）

#### 4. TTSSentenceEndPayload
```python
class TTSSentenceEndPayload(WebPayload, BaseModel):
    data: bytes  # 音频数据（PCM/MP3）
```

**触发时机**：TTS完成一个句子的合成
**前端处理**：播放音频块

#### 5. TTSDonePayload
```python
class TTSDonePayload(WebPayload, BaseModel):
    pass  # 无额外字段
```

**触发时机**：TTS完整响应结束
**前端处理**：允许用户说话

#### 6. BotErrorPayload
```python
class BotErrorPayload(WebPayload, BaseModel):
    error: ErrorEvent

class ErrorEvent(BaseModel):
    code: str      # 错误码（如 "ASR_INIT_UNAVAILABLE"）
    message: str   # 用户可读错误信息
```

**常见错误码**：
- `INVALID_TOKEN`：面试链接无效
- `TOKEN_ALREADY_WAITING`：重复打开页面
- `ASR_INIT_UNAVAILABLE`：语音识别服务不可用

#### 7. QueueEnteredPayload
```python
class QueueEnteredPayload(WebPayload, BaseModel):
    position: int  # 队列位置（从1开始）
    active: int    # 当前活跃数
    limit: int     # 最大并发数
```

**触发时机**：加入排队
**前端处理**：显示"排队中，当前第X位"

#### 8. QueueUpdatePayload
```python
class QueueUpdatePayload(WebPayload, BaseModel):
    position: int         # 当前位置
    active: int           # 活跃数
    limit: int            # 限制数
    eta_seconds: Optional[int]  # 预计等待时间（未实现）
```

**触发时机**：每5秒心跳
**前端处理**：更新排队位置

#### 9. QueueAdmittedPayload
```python
class QueueAdmittedPayload(WebPayload, BaseModel):
    active: int  # 准入时的活跃数
    limit: int   # 限制数
```

**触发时机**：排队成功，即将开始面试
**前端处理**：隐藏排队界面，进入面试流程

#### 10. BotUpdateConfigPayload（前端→后端）
```python
class BotUpdateConfigPayload(WebPayload, BaseModel):
    speaker: Optional[str]  # 新的发音人ID
```

**使用场景**：前端允许用户切换发音人
**后端处理**：更新 `service.tts_speaker`

#### 11. UserAudio（前端→后端）

UserAudio事件的 `data` 字段包含PCM音频字节：
- 格式：16位 PCM，单声道，16kHz采样率
- 每帧：建议20-100ms（320-1600字节）

## 事件序列示例

### 正常对话流程

```
[后端] BOT_READY {session: "uuid-xxx"}
  ↓
[前端] USER_AUDIO {data: <audio_bytes>}
[前端] USER_AUDIO {data: <audio_bytes>}
...
  ↓
[后端] SENTENCE_RECOGNIZED {sentence: "我想问一下..."}
  ↓
[后端] TTS_SENTENCE_START {sentence: "好的，关于这个问题..."}
  ↓
[后端] TTS_SENTENCE_END {data: <audio_data>}
  ↓
[后端] TTS_DONE {}
  ↓ （循环）
```

### 排队流程

```
[后端] QUEUE_ENTERED {position: 3, active: 5, limit: 5}
  ↓ （每5秒）
[后端] QUEUE_UPDATE {position: 2, active: 5, limit: 5}
  ↓
[后端] QUEUE_UPDATE {position: 1, active: 4, limit: 5}
  ↓
[后端] QUEUE_ADMITTED {active: 5, limit: 5}
  ↓
[后端] BOT_READY {session: "uuid-yyy"}
```

### 错误流程

```
[后端] BOT_ERROR {
    error: {
        code: "ASR_INIT_UNAVAILABLE",
        message: "语音识别服务暂时不可用，请检查网络后重试"
    }
}
[后端] CONNECTION_CLOSED
```

## WebEvent.from_payload() 工厂方法

自动根据 Payload 类型选择事件名称：

```python
@classmethod
def from_payload(cls, payload: WebPayload):
    if isinstance(payload, BotReadyPayload):
        return cls(event=BOT_READY, payload=payload)
    elif isinstance(payload, SentenceRecognizedPayload):
        return cls(event=SENTENCE_RECOGNIZED, payload=payload)
    # ... 其他类型
```

## 使用示例

### 后端发送事件

```python
# 发送识别结果
yield WebEvent.from_payload(
    SentenceRecognizedPayload(sentence="你好")
)

# 发送错误
yield WebEvent.from_payload(
    BotErrorPayload(error=ErrorEvent(
        code="TIMEOUT",
        message="请求超时"
    ))
)
```

### 前端处理事件

```javascript
websocket.onmessage = (event) => {
  const webEvent = parseWebEvent(event.data);
  
  switch (webEvent.event) {
    case "BotReady":
      console.log("Session:", webEvent.payload.session);
      enableMicrophone();
      break;
    
    case "SentenceRecognized":
      displayText(webEvent.payload.sentence, "user");
      break;
    
    case "TTSSentenceStart":
      displayText(webEvent.payload.sentence, "bot");
      break;
    
    case "TTSSentenceEnd":
      playAudio(webEvent.data);
      break;
    
    case "BotError":
      showError(webEvent.payload.error.message);
      break;
  }
};
```

## 相关测试

```bash
pytest backend/tests/test_event_payload.py
pytest backend/tests/test_event_serialization.py
```
