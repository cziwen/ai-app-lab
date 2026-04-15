import asyncio

import startup_self_check as ssc


def _make_config(ark_api_key="k"):
    return ssc.RuntimeConfig(
        ark_api_key=ark_api_key,
        llm1_endpoint_id="ep-llm1",
        llm2_endpoint_id="ep-llm2",
        llm3_endpoint_id=None,
        llm1_thinking_type="disabled",
        llm2_thinking_type="disabled",
        llm3_thinking_type="disabled",
        llm1_reasoning_effort=None,
        llm2_reasoning_effort=None,
        llm3_reasoning_effort=None,
        asr_app_id="asr-app",
        asr_access_token="asr-token",
        asr_resource_id="volc.bigasr.sauc.duration",
        asr_ws_url="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        tts_app_id="tts-app",
        tts_access_token="tts-token",
        tts_speaker="spk",
        redis_url="redis://localhost:6379/0",
    )


def _with_config(base: ssc.RuntimeConfig, **kwargs):
    payload = dict(base.__dict__)
    payload.update(kwargs)
    return ssc.RuntimeConfig(**payload)


def test_check_llm1_requires_ark_api_key():
    async def _run():
        config = _make_config(ark_api_key=None)
        result = await ssc.check_llm1(config)
        assert result.ok is False
        assert result.error == "missing ARK_API_KEY"

    asyncio.run(_run())


def test_check_llm1_requires_valid_endpoint():
    async def _run():
        config = _make_config()
        config = ssc.RuntimeConfig(
            ark_api_key=config.ark_api_key,
            llm1_endpoint_id="doubao-seed-2-0-lite-260215",
            llm2_endpoint_id=config.llm2_endpoint_id,
            llm3_endpoint_id=config.llm3_endpoint_id,
            llm1_thinking_type=config.llm1_thinking_type,
            llm2_thinking_type=config.llm2_thinking_type,
            llm3_thinking_type=config.llm3_thinking_type,
            llm1_reasoning_effort=config.llm1_reasoning_effort,
            llm2_reasoning_effort=config.llm2_reasoning_effort,
            llm3_reasoning_effort=config.llm3_reasoning_effort,
            asr_app_id=config.asr_app_id,
            asr_access_token=config.asr_access_token,
            asr_resource_id=config.asr_resource_id,
            asr_ws_url=config.asr_ws_url,
            tts_app_id=config.tts_app_id,
            tts_access_token=config.tts_access_token,
            tts_speaker=config.tts_speaker,
            redis_url=config.redis_url,
        )
        result = await ssc.check_llm1(config)
        assert result.ok is False
        assert result.detail == "LLM1 config invalid"
        assert "invalid LLM1_ENDPOINT_ID" in (result.error or "")

    asyncio.run(_run())


def test_check_llm1_rejects_invalid_thinking_type():
    async def _run():
        config = _make_config()
        config = ssc.RuntimeConfig(
            ark_api_key=config.ark_api_key,
            llm1_endpoint_id=config.llm1_endpoint_id,
            llm2_endpoint_id=config.llm2_endpoint_id,
            llm3_endpoint_id=config.llm3_endpoint_id,
            llm1_thinking_type="oops",
            llm2_thinking_type=config.llm2_thinking_type,
            llm3_thinking_type=config.llm3_thinking_type,
            llm1_reasoning_effort=config.llm1_reasoning_effort,
            llm2_reasoning_effort=config.llm2_reasoning_effort,
            llm3_reasoning_effort=config.llm3_reasoning_effort,
            asr_app_id=config.asr_app_id,
            asr_access_token=config.asr_access_token,
            asr_resource_id=config.asr_resource_id,
            asr_ws_url=config.asr_ws_url,
            tts_app_id=config.tts_app_id,
            tts_access_token=config.tts_access_token,
            tts_speaker=config.tts_speaker,
            redis_url=config.redis_url,
        )
        result = await ssc.check_llm1(config)
        assert result.ok is False
        assert "invalid LLM1_THINKING_TYPE" in (result.error or "")

    asyncio.run(_run())


def test_check_asr_requires_resource_id():
    async def _run():
        config = _make_config()
        config = ssc.RuntimeConfig(
            ark_api_key=config.ark_api_key,
            llm1_endpoint_id=config.llm1_endpoint_id,
            llm2_endpoint_id=config.llm2_endpoint_id,
            llm3_endpoint_id=config.llm3_endpoint_id,
            llm1_thinking_type=config.llm1_thinking_type,
            llm2_thinking_type=config.llm2_thinking_type,
            llm3_thinking_type=config.llm3_thinking_type,
            llm1_reasoning_effort=config.llm1_reasoning_effort,
            llm2_reasoning_effort=config.llm2_reasoning_effort,
            llm3_reasoning_effort=config.llm3_reasoning_effort,
            asr_app_id=config.asr_app_id,
            asr_access_token=config.asr_access_token,
            asr_resource_id=None,
            asr_ws_url=config.asr_ws_url,
            tts_app_id=config.tts_app_id,
            tts_access_token=config.tts_access_token,
            tts_speaker=config.tts_speaker,
            redis_url=config.redis_url,
        )
        result = await ssc.check_asr(config)
        assert result.ok is False
        assert result.error == "missing ASR_RESOURCE_ID"

    asyncio.run(_run())


def test_check_llm1_reports_upgrade_hint_when_sdk_lacks_responses(monkeypatch):
    async def _run():
        monkeypatch.setattr(
            ssc,
            "detect_responses_capability",
            lambda: (
                False,
                "AsyncArk.responses is unavailable; installed=1.0.123, required=5.0.19",
            ),
        )
        result = await ssc.check_llm1(_make_config())
        assert result.ok is False
        assert result.detail == "LLM1 sdk unsupported"
        assert "required=5.0.19" in (result.error or "")

    asyncio.run(_run())


def test_run_startup_self_check_collects_failures(monkeypatch):
    async def _run():
        async def _fake_llm1(config):
            return ssc.CheckResult(ok=True, detail="LLM1 ok")

        async def _fake_llm2(config):
            return ssc.CheckResult(ok=False, detail="LLM2 failed", error="llm2 bad")

        async def _fake_asr(config):
            return ssc.CheckResult(ok=False, detail="ASR failed", error="asr bad")

        async def _fake_tts(config):
            return ssc.CheckResult(ok=True, detail="TTS ok")

        async def _fake_redis(config):
            return ssc.CheckResult(ok=True, detail="Redis ok")

        monkeypatch.setattr(ssc, "check_llm1", _fake_llm1)
        monkeypatch.setattr(ssc, "check_llm2", _fake_llm2)
        monkeypatch.setattr(ssc, "check_asr", _fake_asr)
        monkeypatch.setattr(ssc, "check_tts", _fake_tts)
        monkeypatch.setattr(ssc, "check_redis", _fake_redis)

        report = await ssc.run_startup_self_check(_make_config())
        assert report.ok is False
        assert report.checks["llm1"].ok is True
        assert report.checks["llm2"].ok is False
        assert report.checks["asr"].ok is False
        assert report.checks["redis"].ok is True
        assert report.errors == {"llm2": "llm2 bad", "asr": "asr bad"}

    asyncio.run(_run())


def test_check_redis_requires_url():
    async def _run():
        config = _make_config()
        config = ssc.RuntimeConfig(
            ark_api_key=config.ark_api_key,
            llm1_endpoint_id=config.llm1_endpoint_id,
            llm2_endpoint_id=config.llm2_endpoint_id,
            llm3_endpoint_id=config.llm3_endpoint_id,
            llm1_thinking_type=config.llm1_thinking_type,
            llm2_thinking_type=config.llm2_thinking_type,
            llm3_thinking_type=config.llm3_thinking_type,
            llm1_reasoning_effort=config.llm1_reasoning_effort,
            llm2_reasoning_effort=config.llm2_reasoning_effort,
            llm3_reasoning_effort=config.llm3_reasoning_effort,
            asr_app_id=config.asr_app_id,
            asr_access_token=config.asr_access_token,
            asr_resource_id=config.asr_resource_id,
            asr_ws_url=config.asr_ws_url,
            tts_app_id=config.tts_app_id,
            tts_access_token=config.tts_access_token,
            tts_speaker=config.tts_speaker,
            redis_url=None,
        )
        result = await ssc.check_redis(config)
        assert result.ok is False
        assert result.error == "missing REDIS_URL"

    asyncio.run(_run())


def test_check_stt_skips_when_not_configured():
    async def _run():
        result = await ssc.check_stt(_make_config())
        assert result.ok is True
        assert "skipped" in result.detail

    asyncio.run(_run())


def test_check_stt_requires_complete_config():
    async def _run():
        config = _with_config(
            _make_config(),
            stt_app_id="stt-app",
            stt_access_token=None,
            stt_resource_id="volc.seedasr.auc",
            stt_audio_public_base_url="https://api.example.com",
            stt_audio_signing_secret="secret",
        )
        result = await ssc.check_stt(config)
        assert result.ok is False
        assert result.detail == "STT config invalid"
        assert "STT_ACCESS_TOKEN" in (result.error or "")

    asyncio.run(_run())


def test_check_stt_does_not_probe_on_startup():
    async def _run():
        config = _with_config(
            _make_config(),
            stt_app_id="stt-app",
            stt_access_token="stt-token",
            stt_resource_id="volc.seedasr.auc",
            stt_audio_public_base_url="https://api.example.com",
            stt_audio_signing_secret="secret",
            stt_self_check_audio_url="https://example.com/sample.wav",
        )
        result = await ssc.check_stt(config)
        assert result.ok is True
        assert result.detail == "STT config ok (startup probe disabled)"

    asyncio.run(_run())
