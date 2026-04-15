import asyncio

import pytest

import service
from sauc_asr_client import SaucASRAudio, SaucASRFullServerResponse, SaucASRResult


class _FakeASRClient:
    def __init__(self):
        self.inited = True
        self.close_calls = 0
        self.connect_id = "old-conn"

    async def close(self):
        self.close_calls += 1
        self.inited = False


def _make_service(fake_asr):
    logs = []
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-llm1",
        llm2_endpoint_id="ep-llm2",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        log_fn=logs.append,
    )
    svc.asr_client = fake_asr
    return svc, logs


def _asr_response(
    text: str,
    duration: int = 0,
    stream_connect_id: str = "",
    utterances=None,
):
    if utterances is None:
        utterances = []
    return SaucASRFullServerResponse(
        result=SaucASRResult(text=text, utterances=utterances),
        audio=SaucASRAudio(duration=duration),
        stream_connect_id=stream_connect_id,
    )


def test_load_asr_silence_timeout_ms_clamps_to_safe_max(monkeypatch):
    logs = []
    monkeypatch.setenv("ASR_SILENCE_TIMEOUT_MS", "20000")
    monkeypatch.setattr(service, "INFO", logs.append)

    loaded = service._load_asr_silence_timeout_ms()

    assert loaded == service.MAX_ASR_SILENCE_TIMEOUT_MS
    assert any("clamp to safe max" in line for line in logs)


def test_load_asr_pre_finalize_grace_ms_clamps_to_safe_max(monkeypatch):
    logs = []
    monkeypatch.setenv("ASR_PRE_FINALIZE_GRACE_MS", "9000")
    monkeypatch.setattr(service, "INFO", logs.append)

    loaded = service._load_asr_pre_finalize_grace_ms()

    assert loaded == service.MAX_ASR_PRE_FINALIZE_GRACE_MS
    assert any("clamp to safe max" in line for line in logs)


def test_manual_turn_end_enabled_loader(monkeypatch):
    monkeypatch.setenv("ASR_MANUAL_TURN_END_ENABLED", "true")
    assert service._load_asr_manual_turn_end_enabled() is True

    monkeypatch.setenv("ASR_MANUAL_TURN_END_ENABLED", "false")
    assert service._load_asr_manual_turn_end_enabled() is False


def test_load_asr_segmentation_mode_loader(monkeypatch):
    monkeypatch.setenv("ASR_SEGMENTATION_MODE", "silence")
    assert service._load_asr_segmentation_mode() == "silence"

    monkeypatch.setenv("ASR_SEGMENTATION_MODE", "invalid")
    assert service._load_asr_segmentation_mode() == "semantic"


def test_load_asr_silence_time_ms_clamps(monkeypatch):
    monkeypatch.setenv("ASR_SILENCE_TIME_MS", "100")
    assert service._load_asr_silence_time_ms() == service.MIN_ASR_SILENCE_TIME_MS

    monkeypatch.setenv("ASR_SILENCE_TIME_MS", "9000")
    assert service._load_asr_silence_time_ms() == service.MAX_ASR_SILENCE_TIME_MS


def test_load_asr_end_window_size_ms_clamps(monkeypatch):
    monkeypatch.setenv("ASR_END_WINDOW_SIZE_MS", "100")
    assert (
        service._load_asr_end_window_size_ms()
        == service.MIN_ASR_END_WINDOW_SIZE_MS
    )

    monkeypatch.setenv("ASR_END_WINDOW_SIZE_MS", "9000")
    assert (
        service._load_asr_end_window_size_ms()
        == service.MAX_ASR_END_WINDOW_SIZE_MS
    )


def test_load_asr_use_utterances_loader(monkeypatch):
    monkeypatch.setenv("ASR_USE_UTTERANCES", "true")
    assert service._load_asr_use_utterances() is True

    monkeypatch.setenv("ASR_USE_UTTERANCES", "false")
    assert service._load_asr_use_utterances() is False


def test_finalize_turn_when_silence_hits_threshold(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 50)
        svc.asr_buffer = "边界收尾"
        svc.asr_last_growth_mono_ms = 1000
        monkeypatch.setattr(svc, "_mono_ms", lambda: 1050)

        finalized = await svc._finalize_asr_turn_if_silent()

        assert isinstance(finalized, service.SentenceRecognizedPayload)
        assert finalized.sentence == "边界收尾"
        assert fake.close_calls == 1
        assert any("ASR_TURN_END reason=silence_timeout silence_ms=50" in line for line in logs)

    asyncio.run(_run())


def test_manual_turn_end_skips_silence_timeout(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASR_MANUAL_TURN_END_ENABLED", True)
        monkeypatch.setattr(service, "ASRInterval", 30)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("你好", 100)
            for _ in range(12):
                await asyncio.sleep(0.02)
                yield _asr_response("你好", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        try:
            await asyncio.wait_for(out_iter.__anext__(), timeout=0.25)
            assert False, "manual turn-end should not emit silence_timeout recognized"
        except asyncio.TimeoutError:
            pass

        assert fake.close_calls == 0
        assert all("reason=silence_timeout" not in line for line in logs)

    asyncio.run(_run())


def test_manual_turn_end_still_supports_client_end_answer(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASR_MANUAL_TURN_END_ENABLED", True)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)
        svc.asr_force_finalize_requested = True

        async def _responses():
            yield _asr_response("你好", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "你好"
        assert fake.close_calls == 1
        assert any("ASR_TURN_END reason=client_end_answer" in line for line in logs)

    asyncio.run(_run())


def test_turn_end_on_wall_clock_silence_without_new_packets(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 30)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(service, "ASR_SILENCE_LOG_EVERY_TICKS", 2)

        async def _responses():
            yield _asr_response("你好", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert recognized.sentence == "你好"
        assert fake.close_calls == 1
        assert svc.asr_buffer == ""
        assert svc.asr_no_input_duration == 0
        assert svc.asr_last_growth_mono_ms == 0
        assert any("ASR_TURN_END reason=silence_timeout" in line for line in logs)

    asyncio.run(_run())


def test_no_early_turn_end_when_text_keeps_growing(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 200)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("你", 100)
            await asyncio.sleep(0.02)
            yield _asr_response("你好", 200)
            await asyncio.sleep(0.02)
            yield _asr_response("你好啊", 300)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        try:
            await asyncio.wait_for(out_iter.__anext__(), timeout=0.08)
            assert False, "should not finalize turn before silence timeout"
        except asyncio.TimeoutError:
            pass

        assert fake.close_calls == 0
        assert svc.asr_buffer == "你好啊"

    asyncio.run(_run())


def test_turn_end_on_wall_clock_even_when_text_stops_but_packets_continue(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 60)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("你好", 100)
            for _ in range(10):
                await asyncio.sleep(0.02)
                yield _asr_response("你好", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert recognized.sentence == "你好"
        assert fake.close_calls == 1

    asyncio.run(_run())


def test_finalize_resets_state_and_does_not_emit_duplicate(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 30)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("hello", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)
        assert recognized.sentence == "hello"

        try:
            await asyncio.wait_for(out_iter.__anext__(), timeout=0.08)
            assert False, "should not emit duplicate sentence after state reset"
        except asyncio.TimeoutError:
            pass

        assert fake.close_calls == 1
        assert svc.asr_buffer == ""
        assert svc.asr_no_input_duration == 0
        assert svc.asr_last_duration == 0
        assert svc.asr_last_growth_mono_ms == 0

    asyncio.run(_run())


def test_handler_loop_restarts_when_asr_stream_unexpectedly_ends(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        calls = {"input": 0, "asr": 0}

        async def _fake_send_greeting(_self):
            if False:
                yield None

        async def _fake_handle_input_event(_self, _inputs):
            calls["input"] += 1

            async def _empty():
                if False:
                    yield None

            return _empty()

        async def _fake_handle_asr_response(_self, _responses):
            calls["asr"] += 1
            if calls["asr"] == 1:
                yield service.SentenceRecognizedPayload(sentence="hi")
                return
            await asyncio.sleep(5)
            if False:
                yield service.SentenceRecognizedPayload(sentence="never")

        async def _fake_stream_llm_chat(_self, _text):
            yield "ok"

        async def _fake_handle_tts_response(_self, llm_output):
            async for _ in llm_output:
                pass
            yield service.TTSDonePayload()

        monkeypatch.setattr(
            service.VoiceBotService, "send_greeting", _fake_send_greeting, raising=False
        )
        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_input_event",
            _fake_handle_input_event,
            raising=False,
        )
        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_asr_response",
            _fake_handle_asr_response,
            raising=False,
        )
        monkeypatch.setattr(
            service.VoiceBotService,
            "stream_llm_chat",
            _fake_stream_llm_chat,
            raising=False,
        )
        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_tts_response",
            _fake_handle_tts_response,
            raising=False,
        )

        async def _inputs():
            while True:
                await asyncio.sleep(10)
                if False:
                    yield service.WebEvent(event=service.USER_AUDIO)

        out_iter = svc.handler_loop(_inputs()).__aiter__()
        first = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)
        second = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert first.event == service.SENTENCE_RECOGNIZED
        assert second.event == service.TTS_DONE

        try:
            await asyncio.wait_for(out_iter.__anext__(), timeout=0.12)
            assert False, "handler loop should keep waiting instead of terminating"
        except asyncio.TimeoutError:
            pass

        assert calls["input"] >= 2
        assert calls["asr"] >= 2
        assert any("ASR_STREAM_RESET reason=upstream_closed" in line for line in logs)

    asyncio.run(_run())


def test_force_finalize_turn_immediately_after_client_end_request(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 99999)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)
        svc.asr_force_finalize_requested = True

        async def _responses():
            yield _asr_response("你好", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.2)

        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "你好"
        assert fake.close_calls == 1
        assert any("ASR_TURN_END reason=client_end_answer" in line for line in logs)

    asyncio.run(_run())


def test_force_finalize_request_dropped_when_no_recognized_text(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)
        svc.asr_force_finalize_requested = True

        async def _responses():
            if False:
                yield _asr_response("unused")

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        try:
            await asyncio.wait_for(out_iter.__anext__(), timeout=0.2)
            assert False, "should not emit recognized payload without text"
        except StopAsyncIteration:
            pass

        assert svc.asr_force_finalize_requested is False
        assert any("ASR_TURN_END_REQUEST_DROPPED reason=no_recognized_text" in line for line in logs)

    asyncio.run(_run())


def test_force_finalize_requested_on_client_hangup_event(monkeypatch):
    async def _run():
        class _InputAwareASRClient(_FakeASRClient):
            def stream_asr(self, audio_iter):
                async def _stream():
                    async for _ in audio_iter:
                        pass
                    if False:
                        yield _asr_response("unused")

                return _stream()

        fake = _InputAwareASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(svc, "_ensure_asr_ready", lambda: asyncio.sleep(0, result=True))

        async def _inputs():
            yield service.WebEvent(event=service.CLIENT_HANGUP)

        stream = await svc.handle_input_event(_inputs())
        out_iter = stream.__aiter__()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(out_iter.__anext__(), timeout=0.2)

        assert svc.asr_force_finalize_requested is True
        assert any("ASR_TURN_END_REQUEST source=client_hangup" in line for line in logs)

    asyncio.run(_run())


def test_stream_end_force_finalizes_buffer_without_waiting_silence(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 99999)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("挂断前最后一句", 100)
            return

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.2)

        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "挂断前最后一句"
        assert fake.close_calls == 1
        assert any("ASR_TURN_END_REQUEST source=stream_end" in line for line in logs)
        assert any("ASR_TURN_END reason=client_end_answer" in line for line in logs)

    asyncio.run(_run())


def test_emit_partial_recognition_events_before_final_sentence(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        svc.emit_asr_partial_events = True
        monkeypatch.setattr(service, "ASRInterval", 30)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("你", 100)
            await asyncio.sleep(0.02)
            yield _asr_response("你好", 200)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        first = await asyncio.wait_for(out_iter.__anext__(), timeout=0.2)
        second = await asyncio.wait_for(out_iter.__anext__(), timeout=0.2)
        third = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert isinstance(first, service.SentencePartialRecognizedPayload)
        assert first.sentence == "你"
        assert isinstance(second, service.SentencePartialRecognizedPayload)
        assert second.sentence == "你好"
        assert isinstance(third, service.SentenceRecognizedPayload)
        assert third.sentence == "你好"

    asyncio.run(_run())


def test_drop_stale_packets_after_turn_finalize_until_new_connect_id(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 99999)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)
        svc.asr_force_finalize_requested = True

        async def _responses():
            yield _asr_response("上一题答案", 100, stream_connect_id="old-conn")
            await asyncio.sleep(0.01)
            yield _asr_response("上一题尾包", 120, stream_connect_id="old-conn")
            await asyncio.sleep(0.01)
            yield _asr_response("下一题答案", 140, stream_connect_id="new-conn")
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        first = await asyncio.wait_for(out_iter.__anext__(), timeout=0.3)
        assert isinstance(first, service.SentenceRecognizedPayload)
        assert first.sentence == "上一题答案"

        svc.asr_force_finalize_requested = True
        second = await asyncio.wait_for(out_iter.__anext__(), timeout=0.3)
        assert isinstance(second, service.SentenceRecognizedPayload)
        assert second.sentence == "下一题答案"
        assert any("ASR_STALE_PACKET_DROPPED" in line for line in logs)
        assert any("ASR_STALE_GUARD_CLEARED" in line for line in logs)

    asyncio.run(_run())


def test_stale_guard_compatible_when_response_connect_id_missing(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 99999)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)
        svc.asr_stale_connect_id = "old-conn"
        svc.asr_drop_stale_packets = True
        svc.asr_force_finalize_requested = True

        async def _responses():
            yield _asr_response("兼容路径文本", 100, stream_connect_id="")
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.3)

        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "兼容路径文本"

    asyncio.run(_run())


def test_text_rewrite_refreshes_silence_clock_and_logs(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 120)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("为什么重要啊？因为你要看业绩", 100)
            await asyncio.sleep(0.05)
            yield _asr_response("为什么重要啊？", 100)
            await asyncio.sleep(0.09)
            yield _asr_response("为什么重要啊？", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        pending_next = asyncio.create_task(out_iter.__anext__())
        await asyncio.sleep(0.15)
        assert pending_next.done() is False

        recognized = await asyncio.wait_for(pending_next, timeout=0.4)
        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "为什么重要啊？"
        assert any("ASR_TEXT_REWRITE" in line for line in logs)

    asyncio.run(_run())


def test_silence_timeout_uses_pre_finalize_grace_and_accepts_late_packet(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 50)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 100)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("你好", 100)
            await asyncio.sleep(0.08)
            yield _asr_response("你好啊", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        pending_next = asyncio.create_task(out_iter.__anext__())
        await asyncio.sleep(0.16)
        assert pending_next.done() is False

        recognized = await asyncio.wait_for(pending_next, timeout=0.6)
        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "你好啊"
        assert any("ASR_PRE_FINALIZE_DRAIN_START" in line for line in logs)
        assert any("ASR_PRE_FINALIZE_DRAIN_ABORTED_BY_LATE_PACKET" in line for line in logs)

    asyncio.run(_run())


def test_silence_timeout_grace_expires_without_late_packet(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, logs = _make_service(fake)
        monkeypatch.setattr(service, "ASRInterval", 40)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 60)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response("你好", 100)
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)
        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "你好"
        assert any("ASR_PRE_FINALIZE_DRAIN_START" in line for line in logs)
        assert any("ASR_PRE_FINALIZE_DRAIN_TIMEOUT" in line for line in logs)

    asyncio.run(_run())


def test_utterances_incrementally_append_and_deduplicate(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        monkeypatch.setattr(service, "ASR_USE_UTTERANCES", True)
        monkeypatch.setattr(service, "ASRInterval", 40)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response(
                "我今天想分享",
                100,
                utterances=[{"text": "我今天想分享", "start_time": 0, "end_time": 600}],
            )
            await asyncio.sleep(0.02)
            yield _asr_response(
                "我今天想分享一个项目",
                200,
                utterances=[
                    {"text": "我今天想分享", "start_time": 0, "end_time": 600},
                    {"text": "一个项目", "start_time": 610, "end_time": 1000},
                ],
            )
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "我今天想分享一个项目"

    asyncio.run(_run())


def test_utterances_disabled_falls_back_to_result_text(monkeypatch):
    async def _run():
        fake = _FakeASRClient()
        svc, _ = _make_service(fake)
        monkeypatch.setattr(service, "ASR_USE_UTTERANCES", False)
        monkeypatch.setattr(service, "ASRInterval", 40)
        monkeypatch.setattr(service, "ASR_PRE_FINALIZE_GRACE_MS", 0)
        monkeypatch.setattr(service, "ASR_POLL_INTERVAL_SECONDS", 0.01)

        async def _responses():
            yield _asr_response(
                "回退文本",
                100,
                utterances=[{"text": "", "start_time": 0, "end_time": 300}],
            )
            await asyncio.sleep(3600)

        out_iter = svc.handle_asr_response(_responses()).__aiter__()
        recognized = await asyncio.wait_for(out_iter.__anext__(), timeout=0.5)

        assert isinstance(recognized, service.SentenceRecognizedPayload)
        assert recognized.sentence == "回退文本"

    asyncio.run(_run())
