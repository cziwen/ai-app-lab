import asyncio
import json
import logging
from pathlib import Path

import admin_store
import handler


def _setup_tmp_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    storage_dir = data_dir / "storage"
    audio_dir = storage_dir / "audio"
    interview_log_dir = storage_dir / "interview_logs"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(admin_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_store, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(admin_store, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(admin_store, "INTERVIEW_LOG_DIR", interview_log_dir)
    monkeypatch.setattr(admin_store, "DB_PATH", db_path)


def _reset_handler_loggers(monkeypatch, tmp_path: Path) -> None:
    interview_log_dir = tmp_path / "data" / "storage" / "interview_logs"
    monkeypatch.setattr(handler, "INTERVIEW_LOG_DIR", interview_log_dir)
    for logger in handler._INTERVIEW_LOGGER_CACHE.values():
        for logger_handler in logger.handlers:
            logger_handler.close()
        logger.handlers.clear()
    handler._INTERVIEW_LOGGER_CACHE.clear()
    handler._INTERVIEW_LOGGER_LAST_USED.clear()


def _create_interview_fixture() -> str:
    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试候选人",
        job_uid=job["job_uid"],
        duration_minutes=20,
        notes=None,
    )
    return interview["token"]


async def _send_frontend_logs_request(port: int, path: str, payload) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps(payload).encode("utf-8")
    request = (
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("utf-8") + body
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response.decode("utf-8", errors="replace")


def test_frontend_log_endpoint_requires_valid_token(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            missing_token_response = await _send_frontend_logs_request(
                port, "/api/frontend-logs", ["entry"]
            )
            invalid_token_response = await _send_frontend_logs_request(
                port, "/api/frontend-logs?token=INT-NOT-FOUND", ["entry"]
            )
        finally:
            server.close()
            await server.wait_closed()
        return missing_token_response, invalid_token_response

    missing_token_response, invalid_token_response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in missing_token_response
    assert "HTTP/1.1 400 Bad Request" in invalid_token_response


def test_frontend_log_endpoint_writes_interview_frontend_log(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["line-1", "line-2"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    log_file = tmp_path / "data" / "storage" / "interview_logs" / token / "frontend.log"
    assert "HTTP/1.1 200 OK" in response
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "line-1" in content
    assert "line-2" in content


def test_frontend_log_endpoint_rejects_oversized_body(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "FRONTEND_LOG_MAX_BODY_BYTES", 20)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["0123456789", "9876543210"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in response


def test_frontend_log_endpoint_rejects_too_many_entries(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "FRONTEND_LOG_MAX_ENTRIES", 1)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["line-1", "line-2"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in response


def test_frontend_log_endpoint_rejects_too_long_entry(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "FRONTEND_LOG_MAX_ENTRY_CHARS", 4)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["line-1"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in response


def test_interview_logger_release_clears_cache(monkeypatch, tmp_path):
    _reset_handler_loggers(monkeypatch, tmp_path)
    token = "INT-TEST-RELEASE"
    cache_key = (token, "frontend")
    logger = handler._get_interview_logger(token, "frontend")
    assert cache_key in handler._INTERVIEW_LOGGER_CACHE
    assert cache_key in handler._INTERVIEW_LOGGER_LAST_USED

    released = handler._release_interview_loggers_for_token(token)

    assert released == 1
    assert cache_key not in handler._INTERVIEW_LOGGER_CACHE
    assert cache_key not in handler._INTERVIEW_LOGGER_LAST_USED
    assert logger.handlers == []


def test_interview_logger_cache_prunes_overflow(monkeypatch, tmp_path):
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "INTERVIEW_LOGGER_CACHE_MAX", 1)
    monkeypatch.setattr(handler, "INTERVIEW_LOGGER_IDLE_SECONDS", 999999)

    handler._get_interview_logger("INT-OLD", "frontend")
    handler._get_interview_logger("INT-NEW", "frontend")

    assert len(handler._INTERVIEW_LOGGER_CACHE) == 1
    assert ("INT-NEW", "frontend") in handler._INTERVIEW_LOGGER_CACHE
    assert ("INT-OLD", "frontend") not in handler._INTERVIEW_LOGGER_CACHE


def test_persistence_process_logs_error_on_final_failure(monkeypatch):
    captured_records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    capture_logger = logging.getLogger("test.persistence.logger")
    capture_logger.setLevel(logging.INFO)
    capture_logger.handlers.clear()
    capture_logger.addHandler(CaptureHandler())
    capture_logger.propagate = False

    def _raise_save(*args, **kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(handler, "save_interview_turns", _raise_save)

    queue = handler.PersistenceQueue(capture_logger)
    task = handler.PersistenceTask(
        token="INT-PERSIST-ERR",
        turns=[],
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=b"",
        interview_completed=False,
        candidate_audio_dropped_frames=0,
        retries=handler.PERSISTENCE_MAX_RETRIES,
    )

    asyncio.run(queue._process(task))

    assert any(
        record.levelno == logging.ERROR
        and "event=interview_persist.failed" in record.getMessage()
        for record in captured_records
    )
