# backend/async_log.py

## 模块概述

实现异步日志Handler，避免日志I/O阻塞主线程，支持轮转、队列满处理和优雅关闭。

## 核心类

### AsyncRotatingFileHandler

```python
class AsyncRotatingFileHandler(logging.Handler):
    def __init__(
        self,
        log_path: str,              # 日志文件路径
        max_bytes: int,             # 单文件最大字节数
        backup_count: int,          # 备份文件数量
        log_format: str,            # 日志格式
        queue_size: int = 10000,    # 内存队列大小
        flush_interval_ms: int = 200,  # 刷盘间隔（毫秒）
        drop_policy: str = "drop_oldest",  # 队列满时策略
        close_timeout_seconds: float = 5.0,  # 关闭超时
    )
```

## 工作原理

### 架构图

```
logging.info() → emit()
       ↓
  Queue.put_nowait()
       ↓
  [内存队列: queue.Queue]
       ↓
  Worker线程 (_run)
       ↓
  RotatingFileHandler.emit()
       ↓
  磁盘文件
```

### Worker线程

```python
def _run(self) -> None:
    while not self._stop_event.is_set() or not self._queue.empty():
        try:
            record = self._queue.get(timeout=self._flush_timeout)
        except queue.Empty:
            self._flush_drop_warning()  # 刷新丢弃计数
            continue
        
        self._flush_drop_warning()
        self._sink.emit(record)  # 写入文件
```

**特点**：
- 循环从队列取日志并写入
- 超时后检查并刷新丢弃告警
- 收到停止信号后继续清空队列

## 队列满处理

### drop_policy 策略

#### drop_oldest（默认）

```python
if drop_policy == "drop_oldest":
    try:
        self._queue.get_nowait()  # 丢弃最旧日志
    except queue.Empty:
        return
    try:
        self._queue.put_nowait(record)  # 插入新日志
    except queue.Full:
        self._count_dropped(1)
```

**适用场景**：希望保留最新日志，丢弃历史

#### drop_newest

```python
if drop_policy == "drop_newest":
    self._count_dropped(1)
    return  # 直接丢弃当前日志
```

**适用场景**：队列满时直接放弃新日志

### 丢弃计数与告警

```python
def _flush_drop_warning(self) -> None:
    dropped = self._consume_dropped_count()
    if dropped <= 0:
        return
    warn_record = logging.LogRecord(
        name="async-log",
        level=logging.WARNING,
        msg=f"[AsyncLog] dropped {dropped} log messages due to full queue",
        ...
    )
    self._sink.emit(warn_record)
```

**告警格式**：
```
2025-03-22 12:45:30 - WARNING - [AsyncLog] dropped 150 log messages due to full queue
```

## 优雅关闭

### close() 方法

```python
def close(self) -> None:
    with self._close_lock:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()  # 通知Worker停止
        
        # 等待Worker清空队列
        if self._worker.is_alive():
            self._worker.join(timeout=self._close_timeout_seconds)
        
        # 超时处理
        if self._worker.is_alive():
            remaining = self._queue.qsize()
            dropped = self._consume_dropped_count()
            warn = logging.LogRecord(
                msg=f"[AsyncLog] close timeout timeout_s={self._close_timeout_seconds} "
                    f"remaining_queue={remaining} dropped_unreported={dropped}",
                ...
            )
            self._sink.emit(warn)
        else:
            self._flush_drop_warning()
            self._sink.flush()
        
        self._sink.close()
        super().close()
```

**关闭流程**：
1. 设置停止标志
2. 等待Worker线程退出（最多 `close_timeout_seconds`）
3. 如超时，记录告警并强制关闭
4. 如正常退出，刷新告警并关闭底层Handler

## 配置建议

### 高吞吐场景

```python
queue_size=20000             # 大队列
flush_interval_ms=500        # 降低刷盘频率
drop_policy="drop_oldest"    # 保留最新日志
```

### 低延迟场景

```python
queue_size=5000              # 小队列
flush_interval_ms=100        # 快速刷盘
drop_policy="drop_newest"    # 快速放弃
```

### 关键业务场景

```python
queue_size=50000             # 超大队列
flush_interval_ms=1000       # 批量刷盘
close_timeout_seconds=30     # 长超时，确保不丢日志
```

## 使用示例

### 创建Handler

```python
from async_log import build_async_rotating_handler

handler = build_async_rotating_handler(
    log_path="/var/log/app.log",
    max_bytes=10 * 1024 * 1024,  # 10MB
    backup_count=5,
    log_format="%(asctime)s - %(levelname)s - %(message)s",
    queue_size=10000,
    flush_interval_ms=200,
    drop_policy="drop_oldest",
    close_timeout_seconds=5.0,
)

logger = logging.getLogger("my_app")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
```

### 记录日志

```python
# 非阻塞
logger.info("User logged in")
logger.error("Failed to connect", exc_info=True)
```

### 优雅关闭

```python
import atexit

def cleanup():
    handler.close()  # 等待队列清空

atexit.register(cleanup)
```

## 监控指标

### 队列深度

```python
queue_depth = handler._queue.qsize()
if queue_depth > handler._queue.maxsize * 0.8:
    print("WARNING: Log queue nearly full")
```

### 丢弃率

```bash
grep "AsyncLog.*dropped" logs/backend-*.log
# 如频繁出现，增大 queue_size
```

## 故障排查

### 1. 日志丢失

**检查**：
```bash
grep "AsyncLog.*dropped" logs/*.log
```

**解决**：
- 增大 `queue_size`
- 降低日志量（如采样）
- 增加 `flush_interval_ms`（批量写入）

### 2. 程序退出日志未写入

**原因**：未调用 `handler.close()`

**解决**：
```python
import signal
import sys

def signal_handler(sig, frame):
    handler.close()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
```

### 3. 关闭超时告警

**现象**：
```
[AsyncLog] close timeout timeout_s=5.0 remaining_queue=1000 dropped_unreported=50
```

**解决**：
- 增大 `close_timeout_seconds`
- 减少关闭时的日志量
- 检查磁盘I/O是否缓慢

## 性能对比

### 同步 vs 异步

**同步日志**（RotatingFileHandler）：
```
每次 logging.info() 阻塞 1-5ms（磁盘I/O）
100次调用 = 100-500ms 阻塞
```

**异步日志**（AsyncRotatingFileHandler）：
```
每次 logging.info() 耗时 <0.1ms（内存队列）
100次调用 = <10ms
磁盘I/O在后台线程异步完成
```

## 相关测试

```bash
pytest backend/tests/test_async_log_queue.py
pytest backend/tests/test_async_log_rotation.py
pytest backend/tests/test_async_log_close.py
```
