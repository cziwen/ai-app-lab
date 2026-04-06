# backend/event.py

## 模块概述
`event.py` 定义了前后端 WebSocket 协议中使用的事件常量与 payload 结构，是通信契约的单一来源。

## 当前事件集合（与代码一致）

### 后端 -> 前端
- `BotReady`
- `SentenceRecognized`
- `SentencePartialRecognized`
- `TTSSentenceStart`
- `TTSSentenceEnd`
- `TTSDone`
- `BotError`

### 前端 -> 后端
- `BotUpdateConfig`
- `UserAudio`
- `ClientHangup`
- `ClientEndAnswer`

说明：当前实现已不再使用历史排队事件。并发满载时由后端直接下发 `BotError` 并关闭连接。

## 关键数据结构

### `ErrorEvent`
```python
class ErrorEvent(BaseModel):
    code: str
    message: str
```

### `BotErrorPayload`
```python
class BotErrorPayload(WebPayload, BaseModel):
    error: ErrorEvent
```

常见错误码：
- `INVALID_TOKEN`：面试链接无效或已失效
- `TOKEN_ALREADY_WAITING`：同一 token 在其他页面已占用
- `INTERVIEW_CAPACITY_FULL`：并发已满，快速失败
- `SERVICE_UNAVAILABLE`：服务临时不可用

### `WebEvent`
```python
class WebEvent(BaseModel):
    event: str
    payload: Optional[WebPayload] = None
    data: Optional[bytes] = None
```

## 发送示例
```python
yield WebEvent.from_payload(
    BotErrorPayload(error=ErrorEvent(code="INTERVIEW_CAPACITY_FULL", message="当前面试的人有点多，请稍后再试"))
)
```

## 相关测试
- `pytest backend/tests/test_handler_connection_close_source.py`
- `pytest backend/tests/test_service_interview_llm.py`
