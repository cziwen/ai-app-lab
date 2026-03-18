import logging
import os
import queue
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional


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
    ) -> None:
        super().__init__(level=logging.INFO)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self.drop_policy = (drop_policy or "drop_oldest").strip().lower()
        if self.drop_policy not in ("drop_oldest", "drop_newest"):
            self.drop_policy = "drop_oldest"
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
        self._dropped = 0
        self._warn_pending = False
        self._worker.start()

    @property
    def baseFilename(self) -> str:
        return self._sink.baseFilename

    def emit(self, record: logging.LogRecord) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._queue.put_nowait(record)
            return
        except queue.Full:
            pass

        self._dropped += 1
        self._warn_pending = True
        if self.drop_policy == "drop_newest":
            return
        try:
            self._queue.get_nowait()
        except queue.Empty:
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
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
        if not self._warn_pending or self._dropped <= 0:
            return
        dropped = self._dropped
        self._dropped = 0
        self._warn_pending = False
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

    def close(self) -> None:
        self._stop_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
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
) -> AsyncRotatingFileHandler:
    return AsyncRotatingFileHandler(
        log_path=log_path,
        max_bytes=max_bytes,
        backup_count=backup_count,
        log_format=log_format,
        queue_size=queue_size,
        flush_interval_ms=flush_interval_ms,
        drop_policy=drop_policy,
    )
