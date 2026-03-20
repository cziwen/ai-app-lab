import logging
import threading
import time
from pathlib import Path

from async_log import build_async_rotating_handler


def _build_test_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    return logger


def test_async_log_handler_writes_messages(tmp_path: Path):
    log_path = tmp_path / "async.log"
    logger = _build_test_logger("test.async.log.write")
    handler = build_async_rotating_handler(
        log_path=str(log_path),
        max_bytes=1024 * 1024,
        backup_count=1,
        log_format="%(message)s",
        queue_size=100,
        flush_interval_ms=10,
        drop_policy="drop_oldest",
    )
    logger.addHandler(handler)
    try:
        logger.info("line-1")
        logger.info("line-2")
    finally:
        handler.close()

    content = log_path.read_text(encoding="utf-8")
    assert "line-1" in content
    assert "line-2" in content


def test_async_log_handler_reports_drop_when_queue_is_full(tmp_path: Path):
    log_path = tmp_path / "drop.log"
    logger = _build_test_logger("test.async.log.drop")
    handler = build_async_rotating_handler(
        log_path=str(log_path),
        max_bytes=1024 * 1024,
        backup_count=1,
        log_format="%(message)s",
        queue_size=1,
        flush_interval_ms=10,
        drop_policy="drop_oldest",
    )
    logger.addHandler(handler)
    original_emit = handler._sink.emit

    def _slow_emit(record):
        time.sleep(0.01)
        original_emit(record)

    handler._sink.emit = _slow_emit
    try:
        for idx in range(200):
            logger.info("line-%s", idx)
    finally:
        handler.close()

    content = log_path.read_text(encoding="utf-8")
    assert "[AsyncLog] dropped" in content


def test_async_log_handler_reports_close_timeout(tmp_path: Path):
    log_path = tmp_path / "close-timeout.log"
    logger = _build_test_logger("test.async.log.close_timeout")
    handler = build_async_rotating_handler(
        log_path=str(log_path),
        max_bytes=1024 * 1024,
        backup_count=1,
        log_format="%(message)s",
        queue_size=64,
        flush_interval_ms=10,
        drop_policy="drop_oldest",
        close_timeout_seconds=0.01,
    )
    logger.addHandler(handler)
    original_emit = handler._sink.emit

    def _slow_emit(record):
        time.sleep(0.2)
        original_emit(record)

    handler._sink.emit = _slow_emit
    try:
        for idx in range(40):
            logger.info("line-%s", idx)
    finally:
        handler.close()

    content = log_path.read_text(encoding="utf-8")
    assert "close timeout" in content


def test_async_log_handler_supports_concurrent_emit(tmp_path: Path):
    log_path = tmp_path / "concurrent.log"
    logger = _build_test_logger("test.async.log.concurrent")
    handler = build_async_rotating_handler(
        log_path=str(log_path),
        max_bytes=1024 * 1024,
        backup_count=1,
        log_format="%(message)s",
        queue_size=4096,
        flush_interval_ms=10,
        drop_policy="drop_oldest",
    )
    logger.addHandler(handler)

    def _worker(worker_id: int):
        for i in range(200):
            logger.info("worker-%s line-%s", worker_id, i)

    threads = [threading.Thread(target=_worker, args=(idx,)) for idx in range(4)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        handler.close()

    content = log_path.read_text(encoding="utf-8")
    assert "worker-0 line-0" in content
    assert "worker-3 line-199" in content
