import asyncio

import startup_self_check as ssc


def _make_config(ark_api_key="k"):
    return ssc.RuntimeConfig(
        ark_api_key=ark_api_key,
        llm1_endpoint_id="ep-llm1",
        llm2_endpoint_id="ep-llm2",
        llm1_thinking_type="disabled",
        llm2_thinking_type="disabled",
        llm1_reasoning_effort=None,
        llm2_reasoning_effort=None,
        asr_app_id="asr-app",
        asr_access_token="asr-token",
        tts_app_id="tts-app",
        tts_access_token="tts-token",
        tts_speaker="spk",
    )


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
            llm1_thinking_type=config.llm1_thinking_type,
            llm2_thinking_type=config.llm2_thinking_type,
            llm1_reasoning_effort=config.llm1_reasoning_effort,
            llm2_reasoning_effort=config.llm2_reasoning_effort,
            asr_app_id=config.asr_app_id,
            asr_access_token=config.asr_access_token,
            tts_app_id=config.tts_app_id,
            tts_access_token=config.tts_access_token,
            tts_speaker=config.tts_speaker,
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
            llm1_thinking_type="oops",
            llm2_thinking_type=config.llm2_thinking_type,
            llm1_reasoning_effort=config.llm1_reasoning_effort,
            llm2_reasoning_effort=config.llm2_reasoning_effort,
            asr_app_id=config.asr_app_id,
            asr_access_token=config.asr_access_token,
            tts_app_id=config.tts_app_id,
            tts_access_token=config.tts_access_token,
            tts_speaker=config.tts_speaker,
        )
        result = await ssc.check_llm1(config)
        assert result.ok is False
        assert "invalid LLM1_THINKING_TYPE" in (result.error or "")

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

        monkeypatch.setattr(ssc, "check_llm1", _fake_llm1)
        monkeypatch.setattr(ssc, "check_llm2", _fake_llm2)
        monkeypatch.setattr(ssc, "check_asr", _fake_asr)
        monkeypatch.setattr(ssc, "check_tts", _fake_tts)

        report = await ssc.run_startup_self_check(_make_config())
        assert report.ok is False
        assert report.checks["llm1"].ok is True
        assert report.checks["llm2"].ok is False
        assert report.checks["asr"].ok is False
        assert report.errors == {"llm2": "llm2 bad", "asr": "asr bad"}

    asyncio.run(_run())
