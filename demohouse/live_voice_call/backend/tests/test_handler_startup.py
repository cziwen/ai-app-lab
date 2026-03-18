import asyncio

import handler
from startup_self_check import CheckResult, SelfCheckReport


def _ok_report():
    return SelfCheckReport(
        ok=True,
        checks={
            "llm": CheckResult(ok=True, detail="LLM ok"),
            "asr": CheckResult(ok=True, detail="ASR ok"),
            "tts": CheckResult(ok=True, detail="TTS ok"),
        },
        errors={},
    )


def _fail_report():
    return SelfCheckReport(
        ok=False,
        checks={
            "llm": CheckResult(ok=False, detail="LLM failed", error="missing key"),
            "asr": CheckResult(ok=True, detail="ASR ok"),
            "tts": CheckResult(ok=True, detail="TTS ok"),
        },
        errors={"llm": "missing key"},
    )


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
        monkeypatch.setattr(handler.websockets, "serve", _fake_ws_serve)
        monkeypatch.setattr(asyncio, "start_server", _fake_http_start)
        monkeypatch.setattr(handler, "create_admin_app", lambda: object())
        monkeypatch.setattr(handler.uvicorn, "Server", _ApiServer)

        await handler.main()
        assert called["ws"] is True
        assert called["http"] is True
        assert called["api"] is True

    asyncio.run(_run())
