import logging
from pathlib import Path

from async_log import build_async_rotating_handler


def test_async_log_handler_writes_messages(tmp_path: Path):
    log_path = tmp_path / "async.log"
    logger = logging.getLogger("test.async.log")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
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
