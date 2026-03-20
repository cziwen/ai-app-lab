import logging
import os
import queue
import threading
from logging.handlers import RotatingFileHandler


class AsyncRotatingFileHandler(logging.Handler):
    def __init__(
        self,
        log_path: str,
        max_bytes: int,
        backup_count: int,
        log_format: str,
        queue_size: int = 10000,
        flush_interval_ms: int = 200,
        drop_policy: str = "drop_oldest",
        close_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(level=logging.INFO)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.drop_policy = (drop_policy or "drop_oldest").strip().lower()
        if self.drop_policy not in ("drop_oldest", "drop_newest"):
            self.drop_policy = "drop_oldest"
        self._close_timeout_seconds = max(0.1, float(close_timeout_seconds))
        self._queue: "queue.Queue[logging.LogRecord]" = queue.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._flush_timeout = max(10, int(flush_interval_ms)) / 1000.0
        self._sink = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._sink.setLevel(logging.INFO)
        self._sink.setFormatter(logging.Formatter(log_format))
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._close_lock = threading.Lock()
        self._drop_lock = threading.Lock()
        self._closed = False
        self._dropped = 0
        self._worker.start()

    @property
    def baseFilename(self) -> str:
        return self._sink.baseFilename

    def emit(self, record: logging.LogRecord) -> None:
        if self._stop_event.is_set() or self._closed:
            return
        try:
            self._queue.put_nowait(record)
            return
        except queue.Full:
            pass

        self._count_dropped(1)
        if self.drop_policy == "drop_newest":
            return
        try:
            self._queue.get_nowait()
        except queue.Empty:
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._count_dropped(1)
            return

    def _run(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                record = self._queue.get(timeout=self._flush_timeout)
            except queue.Empty:
                self._flush_drop_warning()
                continue
            self._flush_drop_warning()
            self._sink.emit(record)

        self._flush_drop_warning()
        self._sink.flush()

    def _flush_drop_warning(self) -> None:
        dropped = self._consume_dropped_count()
        if dropped <= 0:
            return
        warn_record = logging.LogRecord(
            name="async-log",
            level=logging.WARNING,
            pathname=__file__,
            lineno=0,
            msg=f"[AsyncLog] dropped {dropped} log messages due to full queue",
            args=(),
            exc_info=None,
        )
        self._sink.emit(warn_record)

    def _count_dropped(self, count: int) -> None:
        if count <= 0:
            return
        with self._drop_lock:
            self._dropped += count

    def _consume_dropped_count(self) -> int:
        with self._drop_lock:
            dropped = self._dropped
            self._dropped = 0
        return dropped

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            if self._worker.is_alive():
                self._worker.join(timeout=self._close_timeout_seconds)

            if self._worker.is_alive():
                remaining = self._queue.qsize()
                dropped = self._consume_dropped_count()
                warn_record = logging.LogRecord(
                    name="async-log",
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=0,
                    msg=(
                        "[AsyncLog] close timeout timeout_s=%s remaining_queue=%s "
                        "dropped_unreported=%s"
                    ),
                    args=(self._close_timeout_seconds, remaining, dropped),
                    exc_info=None,
                )
                self._sink.emit(warn_record)
            else:
                self._flush_drop_warning()
                self._sink.flush()

            self._sink.close()
            super().close()


def build_async_rotating_handler(
    *,
    log_path: str,
    max_bytes: int,
    backup_count: int,
    log_format: str,
    queue_size: int,
    flush_interval_ms: int,
    drop_policy: str,
    close_timeout_seconds: float = 5.0,
) -> AsyncRotatingFileHandler:
    return AsyncRotatingFileHandler(
        log_path=log_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
        log_format=log_format,
        queue_size=queue_size,
        flush_interval_ms=flush_interval_ms,
        drop_policy=drop_policy,
        close_timeout_seconds=close_timeout_seconds,
    )
