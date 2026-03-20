import asyncio
import os
import re

import handler
from startup_self_check import CheckResult, SelfCheckReport


def _ok_report():
    return SelfCheckReport(
        ok=True,
        checks={
            "llm1": CheckResult(ok=True, detail="LLM1 ok"),
            "llm2": CheckResult(ok=True, detail="LLM2 ok"),
            "asr": CheckResult(ok=True, detail="ASR ok"),
            "tts": CheckResult(ok=True, detail="TTS ok"),
        },
        errors={},
    )


def _fail_report():
    return SelfCheckReport(
        ok=False,
        checks={
            "llm1": CheckResult(ok=False, detail="LLM1 failed", error="missing key"),
            "llm2": CheckResult(ok=True, detail="LLM2 ok"),
            "asr": CheckResult(ok=True, detail="ASR ok"),
            "tts": CheckResult(ok=True, detail="TTS ok"),
        },
        errors={"llm1": "missing key"},
    )


def test_server_boot_log_path_uses_timestamp_pid_format():
    base = os.path.basename(handler.SERVER_BOOT_LOG_PATH)
    assert re.match(r"^backend-\d{8}-\d{6}-p\d+\.log$", base)
    assert base != "backend.log"


def test_server_logger_handler_bound_to_boot_log_path_without_duplicates():
    handler._ensure_server_log_handler()
    handler._ensure_server_log_handler()
    matched = [
        logger_handler
        for logger_handler in handler.server_logger.handlers
        if os.path.abspath(getattr(logger_handler, "baseFilename", ""))
        == os.path.abspath(handler.SERVER_BOOT_LOG_PATH)
    ]
    assert len(matched) == 1


def test_handler_main_logs_selected_log_file(monkeypatch):
    async def _run():
        messages = []

        async def _fake_self_check(config):
            return _fail_report()

        def _capture_info(msg, *args, **kwargs):
            text = msg % args if args else msg
            messages.append(text)

        monkeypatch.setattr(handler, "run_startup_self_check", _fake_self_check)
        monkeypatch.setattr(
            handler,
            "PERSISTENCE",
            handler.PersistenceQueue(handler.server_logger),
        )
        monkeypatch.setattr(handler.server_logger, "info", _capture_info)

        try:
            await handler.main()
            assert False, "expected SystemExit"
        except SystemExit:
            pass

        assert any(
            "event=server.log_file.selected path=" in text for text in messages
        )

    asyncio.run(_run())


def test_handler_main_aborts_on_failed_self_check(monkeypatch):
    async def _run():
        called = {"ws": False, "http": False}

        async def _fake_self_check(config):
            return _fail_report()

        async def _fake_ws_serve(*args, **kwargs):
            called["ws"] = True
            raise AssertionError("ws server should not start on failed self check")

        async def _fake_http_start(*args, **kwargs):
            called["http"] = True
            raise AssertionError("http server should not start on failed self check")

        monkeypatch.setattr(handler, "run_startup_self_check", _fake_self_check)
        monkeypatch.setattr(
            handler,
            "PERSISTENCE",
            handler.PersistenceQueue(handler.server_logger),
        )
        monkeypatch.setattr(handler.websockets, "serve", _fake_ws_serve)
        monkeypatch.setattr(asyncio, "start_server", _fake_http_start)

        try:
            await handler.main()
            assert False, "expected SystemExit"
        except SystemExit as e:
            assert e.code == 1
        assert called["ws"] is False
        assert called["http"] is False

    asyncio.run(_run())


def test_handler_main_starts_servers_when_self_check_passes(monkeypatch):
    async def _run():
        called = {"ws": False, "http": False, "api": False}

        class _WsServer:
            async def wait_closed(self):
                return None

        class _HttpServer:
            async def serve_forever(self):
                return None

        class _ApiServer:
            def __init__(self, _config):
                pass

            async def serve(self):
                called["api"] = True
                return None

        async def _fake_self_check(config):
            return _ok_report()

        async def _fake_ws_serve(*args, **kwargs):
            called["ws"] = True
            return _WsServer()

        async def _fake_http_start(*args, **kwargs):
            called["http"] = True
            return _HttpServer()

        monkeypatch.setattr(handler, "run_startup_self_check", _fake_self_check)
        monkeypatch.setattr(
            handler,
            "PERSISTENCE",
            handler.PersistenceQueue(handler.server_logger),
        )
        monkeypatch.setattr(handler.websockets, "serve", _fake_ws_serve)
        monkeypatch.setattr(asyncio, "start_server", _fake_http_start)
        monkeypatch.setattr(handler, "create_admin_app", lambda: object())
        monkeypatch.setattr(handler.uvicorn, "Server", _ApiServer)

        await handler.main()
        assert called["ws"] is True
        assert called["http"] is True
        assert called["api"] is True

    asyncio.run(_run())
