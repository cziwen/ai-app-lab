# backend/llm_limiter.py

## 模块概述

实现基于信号量的LLM并发控制，防止同时发起过多LLM请求导致服务降级或超时。

## 核心实现

### 全局配置

```python
_DEFAULT_LIMIT = int(os.getenv("LLM_CONCURRENT_REQUESTS", "5"))
_limit = max(1, _DEFAULT_LIMIT)
_semaphore = asyncio.Semaphore(_limit)
```

**默认限制**：5个并发请求

### configure_llm_limit()

```python
def configure_llm_limit(limit: int) -> int:
    global _limit, _semaphore
    _limit = max(1, int(limit))
    _semaphore = asyncio.Semaphore(_limit)
    return _limit
```

**调用时机**：handler.py 的 main() 函数启动时

### get_llm_limit()

```python
def get_llm_limit() -> int:
    return _limit
```

**用途**：日志记录和监控

### llm_slot() 上下文管理器

```python
@asynccontextmanager
async def llm_slot() -> AsyncIterator[None]:
    await _semaphore.acquire()  # 获取槽位（可能阻塞）
    try:
        yield
    finally:
        _semaphore.release()      # 释放槽位
```

## 使用示例

### Service中的使用

```python
# InterviewJudge (LLM1)
async with llm_slot():
    result = await adapter.complete_text(
        model=self.llm_endpoint_id,
        instructions=JUDGE_INSTRUCTIONS,
        messages=[...],
    )

# Interviewer LLM (LLM2)
async with llm_slot():
    async for delta_text in adapter.stream_text(
        model=self.llm2_endpoint_id,
        instructions=INTERVIEWER_SYSTEM_PROMPT,
        messages=messages,
    ):
        yield delta_text
```

## 并发控制策略

### 场景分析

**假设配置**：
- `MAX_ACTIVE_INTERVIEWS=5`（最多5场面试）
- `LLM_CONCURRENT_REQUESTS=5`（最多5个LLM请求）

**最坏情况**：
- 5场面试同时进入Judge阶段，占满5个LLM槽位
- 此时任何新的Judge或Interviewer LLM调用都会阻塞

**实际影响**：
- 单场面试最多需要2个LLM槽位（Judge + Interviewer）
- 建议设置 `LLM_CONCURRENT_REQUESTS >= MAX_ACTIVE_INTERVIEWS * 2`

### 推荐配置

| 并发面试数 | LLM限制（保守） | LLM限制（激进） |
|-----------|----------------|----------------|
| 5 | 10 | 15 |
| 10 | 20 | 30 |
| 20 | 40 | 60 |

**保守配置**：每场面试2个槽位，避免阻塞
**激进配置**：假设面试不完全同步，允许更多并发

## 性能优化

### 降低LLM调用延迟

```bash
# 增加并发数（前提：LLM服务端支持）
LLM_CONCURRENT_REQUESTS=20
```

### 避免死锁

**错误示例**（可能死锁）：
```python
async with llm_slot():
    # 外层占用1个槽位
    result1 = await call_llm()
    
    async with llm_slot():
        # 内层尝试再占1个槽位
        result2 = await call_llm()
```

**正确做法**：
```python
# 串行调用，共用同一个槽位
async with llm_slot():
    result1 = await call_llm()
    result2 = await call_llm()
```

## 监控指标

### 槽位饱和度

```python
# 假设Semaphore内部计数器可访问
available_slots = _semaphore._value
total_slots = _limit
saturation = (total_slots - available_slots) / total_slots
```

**告警阈值**：
- 饱和度 > 80% 持续 1分钟：考虑增加 `LLM_CONCURRENT_REQUESTS`
- 饱和度 > 95% 持续 30秒：LLM可能成为瓶颈

### 等待时长

记录 `await _semaphore.acquire()` 的耗时：

```python
start = time.monotonic()
await _semaphore.acquire()
wait_ms = (time.monotonic() - start) * 1000
if wait_ms > 1000:
    logging.warning(f"LLM slot waited {wait_ms}ms")
```

## 故障排查

### 1. LLM响应慢

**检查**：
```bash
# 查看LLM限制配置
grep "llm_limit" logs/backend-*.log

# 当前限制是否过低
```

**解决**：
```bash
LLM_CONCURRENT_REQUESTS=20
```

### 2. 所有面试卡住

**可能原因**：LLM槽位全部被占用且无法释放

**排查**：
```python
# 检查是否有未释放的槽位
# 正常情况下，async with 会自动释放
# 异常情况：程序异常退出但槽位未释放
```

**解决**：重启服务（槽位会重置）

### 3. 槽位永远用不满

**原因**：面试数太少或LLM调用不频繁

**优化**：降低 `LLM_CONCURRENT_REQUESTS` 以节省资源

## 扩展建议

### 分离Judge和Interviewer限制

当前实现共用一个信号量，可改为：

```python
_judge_semaphore = asyncio.Semaphore(LLM1_LIMIT)
_interviewer_semaphore = asyncio.Semaphore(LLM2_LIMIT)

@asynccontextmanager
async def judge_slot():
    await _judge_semaphore.acquire()
    try:
        yield
    finally:
        _judge_semaphore.release()
```

**优势**：Judge和Interviewer互不影响

## 相关测试

```bash
pytest backend/tests/test_llm_limiter_concurrency.py
pytest backend/tests/test_llm_limiter_stress.py
```
