import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import service


class _FakeASRClient:
    def __init__(self, plan):
        self._plan = iter(plan)
        self.inited = False
        self.init_calls = 0
        self.close_calls = 0

    async def init(self):
        self.init_calls += 1
        step = next(self._plan)
        if step == "success":
            self.inited = True
            return
        if step == "hang":
            await asyncio.sleep(3600)
            return
        if isinstance(step, Exception):
            raise step
        raise RuntimeError(f"unsupported test step: {step}")

    async def close(self):
        self.close_calls += 1
        self.inited = False


def _make_service(fake_asr):
    logs = []
    svc = service.VoiceBotService(
        llm_ep_id="ep",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        log_fn=logs.append,
    )
    svc.asr_client = fake_asr
    return svc, logs


def test_ensure_asr_ready_success_first_try():
    async def _run():
        fake = _FakeASRClient(["success"])
        svc, logs = _make_service(fake)

        ok = await svc._ensure_asr_ready()

        assert ok is True
        assert fake.inited is True
        assert fake.init_calls == 1
        assert fake.close_calls == 0
        assert any("ASR_INIT_OK attempt=1" in line for line in logs)

    asyncio.run(_run())


def test_ensure_asr_ready_timeout_then_fail(monkeypatch):
    async def _run():
        fake = _FakeASRClient(["hang", "hang"])
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASR_INIT_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(service, "ASR_INIT_RETRY_BACKOFF_SECONDS", 0)

        ok = await svc._ensure_asr_ready()

        assert ok is False
        assert fake.init_calls == 2
        assert fake.close_calls == 2
        assert any("ASR_INIT_TIMEOUT attempt=1" in line for line in logs)
        assert any("ASR_INIT_TIMEOUT attempt=2" in line for line in logs)
        assert any("ASR_INIT_FAIL attempts=2" in line for line in logs)

    asyncio.run(_run())


def test_ensure_asr_ready_timeout_then_success(monkeypatch):
    async def _run():
        fake = _FakeASRClient(["hang", "success"])
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASR_INIT_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(service, "ASR_INIT_RETRY_BACKOFF_SECONDS", 0)

        ok = await svc._ensure_asr_ready()

        assert ok is True
        assert fake.inited is True
        assert fake.init_calls == 2
        assert fake.close_calls == 1
        assert any("ASR_INIT_RETRY next_attempt=2" in line for line in logs)
        assert any("ASR_INIT_OK attempt=2" in line for line in logs)

    asyncio.run(_run())


def test_ensure_asr_ready_exception_both_attempts(monkeypatch):
    async def _run():
        fake = _FakeASRClient([RuntimeError("boom1"), RuntimeError("boom2")])
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASR_INIT_RETRY_BACKOFF_SECONDS", 0)

        ok = await svc._ensure_asr_ready()

        assert ok is False
        assert fake.init_calls == 2
        assert fake.close_calls == 2
        assert any("ASR_INIT_FAIL attempt=1 stage=init error=boom1" in line for line in logs)
        assert any("ASR_INIT_FAIL attempt=2 stage=init error=boom2" in line for line in logs)
        assert any("ASR_INIT_FAIL attempts=2" in line for line in logs)

    asyncio.run(_run())
