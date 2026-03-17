import asyncio

import startup_self_check as ssc


def _make_config(ark_api_key="k"):
    return ssc.RuntimeConfig(
        ark_api_key=ark_api_key,
        llm_endpoint_id="ep-test",
        asr_app_id="asr-app",
        asr_access_token="asr-token",
        tts_app_id="tts-app",
        tts_access_token="tts-token",
        tts_speaker="spk",
    )


def test_check_llm_requires_ark_api_key():
    async def _run():
        config = _make_config(ark_api_key=None)
        result = await ssc.check_llm(config)
        assert result.ok is False
        assert result.error == "missing ARK_API_KEY"

    asyncio.run(_run())


def test_run_startup_self_check_collects_failures(monkeypatch):
    async def _run():
        async def _fake_llm(config):
            return ssc.CheckResult(ok=True, detail="LLM ok")

        async def _fake_asr(config):
            return ssc.CheckResult(ok=False, detail="ASR failed", error="asr bad")

        async def _fake_tts(config):
            return ssc.CheckResult(ok=True, detail="TTS ok")

        monkeypatch.setattr(ssc, "check_llm", _fake_llm)
        monkeypatch.setattr(ssc, "check_asr", _fake_asr)
        monkeypatch.setattr(ssc, "check_tts", _fake_tts)

        report = await ssc.run_startup_self_check(_make_config())
        assert report.ok is False
        assert report.checks["llm"].ok is True
        assert report.checks["asr"].ok is False
        assert report.errors == {"asr": "asr bad"}

    asyncio.run(_run())
