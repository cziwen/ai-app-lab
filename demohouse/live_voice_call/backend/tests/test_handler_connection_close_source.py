import asyncio
from types import SimpleNamespace

import admin_store
import handler
from event import TTSDonePayload, WebEvent


class _ListLogger:
    def __init__(self):
        self.lines = []

    def info(self, msg, *args, **kwargs):
        text = msg % args if args else msg
        self.lines.append(text)


class _FakeAdmission:
    def __init__(self):
        self.released = []

    async def acquire_or_enqueue(self, _token):
        return True, None, False

    async def release(self, token):
        self.released.append(token)


class _FakePersistence:
    def __init__(self):
        self.tasks = []

    async def submit(self, task):
        self.tasks.append(task)


class _FakeWebSocket:
    def __init__(self, *, close_exc_cls, fail_on_send_call=None):
        self.remote_address = ("127.0.0.1", 8888)
        self.closed = False
        self._send_calls = 0
        self._close_exc_cls = close_exc_cls
        self._fail_on_send_call = fail_on_send_call

    async def send(self, _data):
        self._send_calls += 1
        if (
            self._fail_on_send_call is not None
            and self._send_calls >= self._fail_on_send_call
        ):
            self.closed = True
            raise self._close_exc_cls("client websocket closed")

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.closed = True
        raise StopAsyncIteration


def _fake_session_data(token: str):
    return admin_store.InterviewSessionData(
        token=token,
        candidate_name="test",
        job_uid="job-1",
        job_name="job",
        status=admin_store.INTERVIEW_STATUS_IN_PROGRESS,
        questions=[
            {
                "question_id": "q1",
                "main_question": "介绍一下自己",
                "evidence": {"scoring_boundary": "test"},
            }
        ],
    )


def test_handler_close_source_client_ws(monkeypatch):
    class DummyConnectionClosed(Exception):
        pass

    class _FakeService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-CLIENT-WS"
        ws = _FakeWebSocket(
            close_exc_cls=DummyConnectionClosed,
            fail_on_send_call=2,  # BotReady succeeds; first output send fails.
        )

        monkeypatch.setattr(
            handler.websockets.exceptions, "ConnectionClosed", DummyConnectionClosed
        )
        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "ADMISSION", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "_release_interview_loggers_for_token", lambda _t: 1)
        monkeypatch.setattr(handler, "_get_interview_logger", lambda *_args: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert fake_admission.released == [token]
        assert len(fake_persistence.tasks) == 1
        assert any("Connection closed source=client_ws" in line for line in interview_logger.lines)
        assert any(
            "[Session] closed status=disconnected close_source=client_ws" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_close_source_asr_upstream(monkeypatch):
    class DummyConnectionClosed(Exception):
        pass

    class _FakeService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            raise DummyConnectionClosed("asr upstream closed")
            if False:
                yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-ASR-UPSTREAM"
        ws = _FakeWebSocket(close_exc_cls=DummyConnectionClosed, fail_on_send_call=None)

        monkeypatch.setattr(
            handler.websockets.exceptions, "ConnectionClosed", DummyConnectionClosed
        )
        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "ADMISSION", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "_release_interview_loggers_for_token", lambda _t: 1)
        monkeypatch.setattr(handler, "_get_interview_logger", lambda *_args: interview_logger)
        monkeypatch.setattr(handler, "server_logger", SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None))

        await handler.handler(ws, f"/?token={token}")

        assert fake_admission.released == [token]
        assert len(fake_persistence.tasks) == 1
        assert any("Connection closed source=asr_upstream" in line for line in interview_logger.lines)
        assert any(
            "[Session] closed status=disconnected close_source=asr_upstream" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())
