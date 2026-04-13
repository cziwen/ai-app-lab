import asyncio

import service
from scripted_tts_memory_cache import CachedTTSSentence, ScriptedTTSMemoryCache


def _new_service(*, token: str, on_audio=None, on_bot_text=None):
    return service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_token=token,
        on_bot_audio_chunk=on_audio,
        on_bot_sentence=on_bot_text,
    )


async def _collect_events(async_iterable):
    events = []
    async for item in async_iterable:
        events.append(item)
    return events


def test_send_scripted_text_cache_hit_replays_audio_and_skips_tts(monkeypatch):
    async def _run():
        emitted_audio = []
        emitted_bot_text = []
        cache = ScriptedTTSMemoryCache(ttl_seconds=60, max_entries=10)
        svc = _new_service(
            token="token-a",
            on_audio=lambda chunk: emitted_audio.append(chunk),
            on_bot_text=lambda text: emitted_bot_text.append(text),
        )
        svc.scripted_tts_cache = cache

        stored = cache.set(
            "token-a",
            svc.tts_speaker,
            "脚本问题",
            [
                CachedTTSSentence(transcript="第一句", audio=b"\x01\x02"),
                CachedTTSSentence(transcript="第二句", audio=b"\x03"),
            ],
        )
        assert stored is True

        async def _should_not_call_tts(_self, _stream):
            raise AssertionError("handle_tts_response should not be called on cache hit")

        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_tts_response",
            _should_not_call_tts,
            raising=False,
        )

        events = await _collect_events(svc._send_scripted_text("脚本问题"))
        assert [event.event for event in events] == [
            service.TTS_SENTENCE_START,
            service.TTS_SENTENCE_END,
            service.TTS_SENTENCE_START,
            service.TTS_SENTENCE_END,
            service.TTS_DONE,
        ]
        assert events[0].payload.sentence == "第一句"
        assert events[1].data == b"\x01\x02"
        assert events[2].payload.sentence == "第二句"
        assert events[3].data == b"\x03"
        assert emitted_audio == [b"\x01\x02", b"\x03"]
        assert emitted_bot_text == ["脚本问题"]
        assert svc.history_messages[-1].content == "脚本问题"

    asyncio.run(_run())


def test_send_scripted_text_cache_miss_stores_then_hits(monkeypatch):
    async def _run():
        cache = ScriptedTTSMemoryCache(ttl_seconds=60, max_entries=10)
        svc = _new_service(token="token-b")
        svc.scripted_tts_cache = cache
        handle_tts_calls = {"count": 0}

        async def _fake_handle_tts_response(_self, _stream):
            handle_tts_calls["count"] += 1
            yield service.TTSSentenceStartPayload(sentence="缓存句子")
            yield service.TTSSentenceEndPayload(data=b"\x10\x11")
            yield service.TTSDonePayload()

        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_tts_response",
            _fake_handle_tts_response,
            raising=False,
        )

        first = await _collect_events(svc._send_scripted_text("  需要缓存  "))
        assert handle_tts_calls["count"] == 1
        assert [event.event for event in first] == [
            service.TTS_SENTENCE_START,
            service.TTS_SENTENCE_END,
            service.TTS_DONE,
        ]

        cached = cache.get("token-b", svc.tts_speaker, "需要缓存")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].transcript == "缓存句子"
        assert cached[0].audio == b"\x10\x11"

        second = await _collect_events(svc._send_scripted_text("需要缓存"))
        assert handle_tts_calls["count"] == 1
        assert [event.event for event in second] == [
            service.TTS_SENTENCE_START,
            service.TTS_SENTENCE_END,
            service.TTS_DONE,
        ]
        assert second[0].payload.sentence == "缓存句子"
        assert second[1].data == b"\x10\x11"

    asyncio.run(_run())


def test_send_scripted_text_cache_errors_degrade_to_tts(monkeypatch):
    async def _run():
        svc = _new_service(token="token-c")
        handle_tts_calls = {"count": 0}

        class _BrokenCache:
            def get(self, _token, _speaker, _text):
                raise RuntimeError("cache get failed")

            def set(self, _token, _speaker, _text, _sentences):
                raise RuntimeError("cache set failed")

        svc.scripted_tts_cache = _BrokenCache()

        async def _fake_handle_tts_response(_self, _stream):
            handle_tts_calls["count"] += 1
            yield service.TTSSentenceStartPayload(sentence="fallback")
            yield service.TTSSentenceEndPayload(data=b"\xaa")
            yield service.TTSDonePayload()

        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_tts_response",
            _fake_handle_tts_response,
            raising=False,
        )

        events = await _collect_events(svc._send_scripted_text("fallback"))
        assert handle_tts_calls["count"] == 1
        assert [event.event for event in events] == [
            service.TTS_SENTENCE_START,
            service.TTS_SENTENCE_END,
            service.TTS_DONE,
        ]
        assert events[0].payload.sentence == "fallback"
        assert events[1].data == b"\xaa"

    asyncio.run(_run())
