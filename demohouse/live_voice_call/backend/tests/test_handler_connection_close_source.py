import asyncio
import time
from types import SimpleNamespace

import admin_store
import handler
import pytest
from event import TTSDonePayload, WebEvent


class _ListLogger:
    def __init__(self):
        self.lines = []

    def info(self, msg, *args, **kwargs):
        text = msg % args if args else msg
        self.lines.append(text)


class _FakeAdmission:
    def __init__(self, acquire_result="admitted", current_owner=None):
        self.released = []
        self.acquire_result = acquire_result
        self.current_owner_value = current_owner

    config = SimpleNamespace(heartbeat_seconds=1000, ttl_seconds=30)

    def acquire(self, _token, _owner_id, _client_id=None):
        return self.acquire_result

    def heartbeat(self, _token, _owner_id, _client_id=None):
        return "ok"

    def release(self, token, _owner_id):
        self.released.append(token)
        return True

    def current_owner(self, _token):
        return self.current_owner_value


class _FakePersistence:
    def __init__(self):
        self.tasks = []

    async def submit(self, task):
        self.tasks.append(task)


class _FakeRuntimeCheckpoints:
    def __init__(self, initial=None, save_delay_seconds=0.0):
        self.initial = initial
        self.saved = []
        self.deleted = []
        self.operations = []
        self.save_delay_seconds = save_delay_seconds

    def load(self, _token):
        return self.initial

    def save(self, token, checkpoint):
        if self.save_delay_seconds > 0:
            time.sleep(self.save_delay_seconds)
        self.saved.append((token, checkpoint))
        self.operations.append(("save", token, checkpoint))
        return True

    def delete(self, token):
        self.deleted.append(token)
        self.operations.append(("delete", token))
        return True


class _FakeWebSocket:
    def __init__(self, *, close_exc_cls, fail_on_send_call=None):
        self.remote_address = ("127.0.0.1", 8888)
        self.closed = False
        self.close_code = None
        self.close_reason = ""
        self._send_calls = 0
        self._close_exc_cls = close_exc_cls
        self._fail_on_send_call = fail_on_send_call
        self.sent_messages = []

    async def send(self, data):
        self._send_calls += 1
        self.sent_messages.append(data)
        if (
            self._fail_on_send_call is not None
            and self._send_calls >= self._fail_on_send_call
        ):
            self.closed = True
            raise self._close_exc_cls("client websocket closed")

    async def close(self, code=None, reason=None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason or ""

    async def wait_closed(self):
        while not self.closed:
            await asyncio.sleep(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.closed = True
        raise StopAsyncIteration


@pytest.fixture(autouse=True)
def _patch_start_attempt(monkeypatch):
    monkeypatch.setattr(
        handler,
        "start_interview_attempt",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        handler,
        "save_interview_checkpoint_journal",
        lambda *_args, **_kwargs: True,
    )


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
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert fake_admission.released == [token]
        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is False
        assert any("Connection closed source=client_ws" in line for line in interview_logger.lines)
        assert any(
            "[Session] closed status=disconnected close_source=client_ws" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_closes_ws_with_completed_code_on_normal_end(monkeypatch):
    class _FakeService:
        def __init__(self, **kwargs):
            self._completed_cb = kwargs.get("on_interview_completed")

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            yield WebEvent.from_payload(TTSDonePayload())
            if callable(self._completed_cb):
                self._completed_cb()

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-NORMAL-END-CLOSE-CODE"
        ws = _FakeWebSocket(close_exc_cls=RuntimeError)

        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert ws.close_code == handler.INTERVIEW_COMPLETED_CLOSE_CODE
        assert ws.close_reason == handler.INTERVIEW_COMPLETED_CLOSE_REASON
        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is True
        assert any(
            "event=session.normal_end_close_request" in line for line in interview_logger.lines
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
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)
        monkeypatch.setattr(handler, "server_logger", SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None))

        await handler.handler(ws, f"/?token={token}")

        assert fake_admission.released == [token]
        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is False
        assert any("Connection closed source=asr_upstream" in line for line in interview_logger.lines)
        assert any(
            "[Session] closed status=disconnected close_source=asr_upstream" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_skips_completed_when_token_reacquired(monkeypatch):
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
        fake_admission = _FakeAdmission(current_owner="NEW-OWNER")
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-REACQUIRED"
        ws = _FakeWebSocket(
            close_exc_cls=DummyConnectionClosed,
            fail_on_send_call=2,  # BotReady succeeds; first output send fails.
        )
        mark_disconnected_calls = {"count": 0}

        def _mark_disconnected(*_args, **_kwargs):
            mark_disconnected_calls["count"] += 1
            return True

        monkeypatch.setattr(
            handler.websockets.exceptions, "ConnectionClosed", DummyConnectionClosed
        )
        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(handler, "mark_interview_disconnected", _mark_disconnected)
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert mark_disconnected_calls["count"] == 0
        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is False
        assert fake_persistence.tasks[0].completed_reason is None
        assert any(
            "event=interview.disconnect_mark_skipped reason=token_reacquired action=skip_complete"
            in line
            for line in interview_logger.lines
        )
        assert any(
            "[Session] closed status=disconnected close_source=client_ws" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_rejects_when_capacity_full(monkeypatch):
    async def _run():
        fake_occupancy = _FakeAdmission(acquire_result="capacity_full")
        token = "INT-HANDLER-CAPACITY-FULL"
        ws = _FakeWebSocket(close_exc_cls=RuntimeError)

        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(handler, "OCCUPANCY", fake_occupancy)
        monkeypatch.setattr(handler, "PERSISTENCE", _FakePersistence())
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())

        await handler.handler(ws, f"/?token={token}")

        assert ws.closed is True
        assert len(ws.sent_messages) == 1

    asyncio.run(_run())


def test_handler_rejects_when_token_already_occupied(monkeypatch):
    async def _run():
        fake_occupancy = _FakeAdmission(acquire_result="duplicate_token")
        token = "INT-HANDLER-DUPLICATE"
        ws = _FakeWebSocket(close_exc_cls=RuntimeError)

        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(handler, "OCCUPANCY", fake_occupancy)
        monkeypatch.setattr(handler, "PERSISTENCE", _FakePersistence())
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())

        await handler.handler(ws, f"/?token={token}")

        assert ws.closed is True
        assert len(ws.sent_messages) == 1

    asyncio.run(_run())


def test_handler_marks_hangup_reason_when_client_hangup_event_received(monkeypatch):
    observed_input_events = []

    class _OneMessageWebSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__(close_exc_cls=RuntimeError)
            self._count = 0

        async def __anext__(self):
            if self._count == 0:
                self._count += 1
                return b"ignored"
            self.closed = True
            raise StopAsyncIteration

    class _FakeService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, inputs):
            async for event in inputs:
                observed_input_events.append(str(getattr(event, "event", "")))
                break
            if False:
                yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-HANGUP"
        ws = _OneMessageWebSocket()

        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)
        monkeypatch.setattr(
            handler,
            "convert_binary_to_web_event_to_binary",
            lambda _data: WebEvent(event=handler.CLIENT_HANGUP_EVENT),
        )

        await handler.handler(ws, f"/?token={token}")

        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].completed_reason == "hangup"
        assert observed_input_events == [handler.CLIENT_END_ANSWER_EVENT]
        assert any("event=session.client_hangup" in line for line in interview_logger.lines)

    asyncio.run(_run())


def test_handler_marks_hangup_reason_from_ws_close_frame_fallback(monkeypatch):
    class _ClientHangupWebSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__(close_exc_cls=RuntimeError)
            self.close_code = handler.CLIENT_HANGUP_CLOSE_CODE
            self.close_reason = handler.CLIENT_HANGUP_CLOSE_REASON

    class _FakeService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            if False:
                yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-HANGUP-CLOSE-FRAME"
        ws = _ClientHangupWebSocket()

        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is True
        assert fake_persistence.tasks[0].completed_reason == "hangup"
        assert any(
            "event=session.client_hangup source=ws_close_frame" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_closes_ws_with_hangup_code_when_service_requests_hangup(monkeypatch):
    class _FakeService:
        def __init__(self, **kwargs):
            self._hangup_cb = kwargs.get("on_client_hangup_requested")

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            if callable(self._hangup_cb):
                self._hangup_cb()
            if False:
                yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-SERVICE-HANGUP-CLOSE-CODE"
        ws = _FakeWebSocket(close_exc_cls=RuntimeError)

        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert ws.close_code == handler.CLIENT_HANGUP_CLOSE_CODE
        assert ws.close_reason == handler.CLIENT_HANGUP_CLOSE_REASON
        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].completed_reason == "hangup"
        assert any("event=session.hangup_close_request" in line for line in interview_logger.lines)

    asyncio.run(_run())


def test_handler_marks_hangup_reason_from_ws_close_frame_when_close_source_is_asr_upstream(
    monkeypatch,
):
    class DummyConnectionClosed(Exception):
        pass

    class _AsrUpstreamCloseWebSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__(close_exc_cls=DummyConnectionClosed)
            self.close_code = handler.CLIENT_HANGUP_CLOSE_CODE
            self.close_reason = handler.CLIENT_HANGUP_CLOSE_REASON

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
        token = "INT-HANDLER-HANGUP-CLOSE-FRAME-ASR-UPSTREAM"
        ws = _AsrUpstreamCloseWebSocket()

        monkeypatch.setattr(
            handler.websockets.exceptions, "ConnectionClosed", DummyConnectionClosed
        )
        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is True
        assert fake_persistence.tasks[0].completed_reason == "hangup"
        assert any(
            "event=session.client_hangup source=ws_close_frame" in line
            for line in interview_logger.lines
        )
        assert any(
            "[Session] closed status=completed close_source=asr_upstream" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_does_not_mark_hangup_from_close_reason_only(monkeypatch):
    class _CloseReasonOnlyWebSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__(close_exc_cls=RuntimeError)
            self.close_code = None
            self.close_reason = handler.CLIENT_HANGUP_CLOSE_REASON

    class _FakeService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            async for _ in _inputs:
                break
            if False:
                yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-HANGUP-CLOSE-REASON-ONLY"
        ws = _CloseReasonOnlyWebSocket()

        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].should_mark_completed is False
        assert fake_persistence.tasks[0].completed_reason is None
        assert not any(
            "event=session.client_hangup source=ws_close_frame" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_marks_error_reason_after_close_source_normalization(monkeypatch):
    class _FakeService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            if False:
                yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-CLOSE-SOURCE-NORMALIZE"
        ws = _FakeWebSocket(close_exc_cls=RuntimeError)
        ws.closed = False

        monkeypatch.setattr(handler, "VoiceBotService", _FakeService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: False
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert len(fake_persistence.tasks) == 1
        assert fake_persistence.tasks[0].completed_reason == "error"
        assert any(
            "[Session] closed status=completed close_source=internal_error" in line
            for line in interview_logger.lines
        )

    asyncio.run(_run())


def test_handler_releases_when_wait_closed_finishes_first(monkeypatch):
    class _WaitFirstWebSocket(_FakeWebSocket):
        def __init__(self):
            super().__init__(close_exc_cls=RuntimeError)
            self.closed = False

        async def wait_closed(self):
            self.closed = True
            return None

    class _NeverEndingService:
        def __init__(self, **_kwargs):
            pass

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            while True:
                await asyncio.sleep(0.01)
                if False:
                    yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        token = "INT-HANDLER-WAIT-CLOSED-FIRST"
        ws = _WaitFirstWebSocket()

        monkeypatch.setattr(handler, "VoiceBotService", _NeverEndingService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(
            handler, "mark_interview_disconnected", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", _FakeRuntimeCheckpoints())
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert fake_admission.released == [token]
        assert len(fake_persistence.tasks) == 1

    asyncio.run(_run())


def test_handler_waits_for_latest_checkpoint_flush_before_delete(monkeypatch):
    class _CheckpointService:
        def __init__(self, **kwargs):
            self._checkpoint_cb = kwargs.get("on_interview_runtime_checkpoint")
            self._completed_cb = kwargs.get("on_interview_completed")

        async def init(self):
            return None

        async def handler_loop(self, _inputs):
            if callable(self._checkpoint_cb):
                self._checkpoint_cb({"version": 1})
                self._checkpoint_cb({"version": 2})
            if callable(self._completed_cb):
                self._completed_cb()
            yield WebEvent.from_payload(TTSDonePayload())

    async def _run():
        interview_logger = _ListLogger()
        fake_admission = _FakeAdmission()
        fake_persistence = _FakePersistence()
        runtime_checkpoints = _FakeRuntimeCheckpoints(save_delay_seconds=0.02)
        token = "INT-HANDLER-CHECKPOINT-FLUSH"
        ws = _FakeWebSocket(close_exc_cls=RuntimeError, fail_on_send_call=None)

        monkeypatch.setattr(handler, "VoiceBotService", _CheckpointService)
        monkeypatch.setattr(
            handler,
            "start_interview_session",
            lambda incoming_token: _fake_session_data(incoming_token),
        )
        monkeypatch.setattr(handler, "mark_interview_in_progress", lambda _token: True)
        monkeypatch.setattr(handler, "OCCUPANCY", fake_admission)
        monkeypatch.setattr(handler, "PERSISTENCE", fake_persistence)
        monkeypatch.setattr(handler, "RUNTIME_CHECKPOINTS", runtime_checkpoints)
        monkeypatch.setattr(handler, "_release_interview_logger_owner", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(handler, "_acquire_interview_logger", lambda *_args, **_kwargs: interview_logger)

        await handler.handler(ws, f"/?token={token}")

        assert runtime_checkpoints.saved
        assert runtime_checkpoints.saved[-1][1].get("version") == 2
        delete_positions = [
            idx
            for idx, op in enumerate(runtime_checkpoints.operations)
            if op and op[0] == "delete"
        ]
        assert delete_positions
        first_delete_idx = delete_positions[0]
        assert all(
            op[0] != "save" for op in runtime_checkpoints.operations[first_delete_idx + 1 :]
        )

    asyncio.run(_run())
