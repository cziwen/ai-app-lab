import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from arkitect.core.component.asr import AsyncASRClient
from arkitect.core.component.llm import BaseChatLanguageModel
from arkitect.core.component.llm.model import ArkMessage
from arkitect.core.component.tts import AsyncTTSClient, AudioParams, ConnectionParams
from arkitect.core.component.tts.constants import EventSessionFinished

from prompt import VoiceBotPrompt

DEFAULT_LLM_ENDPOINT_ID = "ep-m-20260315140910-pfztd"
DEFAULT_ASR_APP_ID = "2057385740"
DEFAULT_ASR_ACCESS_TOKEN = "bnO29ab2sIHtKyt3f-Dn8SAYaMZr04BP"
DEFAULT_TTS_APP_ID = "2057385740"
DEFAULT_TTS_ACCESS_TOKEN = "bnO29ab2sIHtKyt3f-Dn8SAYaMZr04BP"
DEFAULT_TTS_SPEAKER = "zh_female_sajiaonvyou_moon_bigtts"


@dataclass(frozen=True)
class RuntimeConfig:
    ark_api_key: Optional[str]
    llm_endpoint_id: str
    asr_app_id: str
    asr_access_token: str
    tts_app_id: str
    tts_access_token: str
    tts_speaker: str


@dataclass
class CheckResult:
    ok: bool
    detail: str
    error: Optional[str] = None


@dataclass
class SelfCheckReport:
    ok: bool
    checks: Dict[str, CheckResult] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


def _env(key: str, default: str) -> str:
    return os.environ.get(key) or default


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        ark_api_key=os.environ.get("ARK_API_KEY"),
        llm_endpoint_id=_env("LLM_ENDPOINT_ID", DEFAULT_LLM_ENDPOINT_ID),
        asr_app_id=_env("ASR_APP_ID", DEFAULT_ASR_APP_ID),
        asr_access_token=_env("ASR_ACCESS_TOKEN", DEFAULT_ASR_ACCESS_TOKEN),
        tts_app_id=_env("TTS_APP_ID", DEFAULT_TTS_APP_ID),
        tts_access_token=_env("TTS_ACCESS_TOKEN", DEFAULT_TTS_ACCESS_TOKEN),
        tts_speaker=_env("TTS_SPEAKER", DEFAULT_TTS_SPEAKER),
    )


async def check_llm(config: RuntimeConfig) -> CheckResult:
    if not config.ark_api_key:
        return CheckResult(
            ok=False,
            detail="ARK_API_KEY missing",
            error="missing ARK_API_KEY",
        )

    messages = [ArkMessage(**{"role": "user", "content": "你好，回复一句话即可。"})]
    llm = BaseChatLanguageModel(
        template=VoiceBotPrompt(),
        messages=messages,
        endpoint_id=config.llm_endpoint_id,
    )
    try:
        has_content = False
        async for chunk in llm.astream():
            if (
                chunk.choices
                and chunk.choices[0].delta
                and chunk.choices[0].delta.content
            ):
                has_content = True
                break
        if has_content:
            return CheckResult(ok=True, detail="LLM ok")
        return CheckResult(
            ok=False,
            detail="LLM no content",
            error="no content from LLM stream",
        )
    except Exception as e:
        return CheckResult(ok=False, detail="LLM failed", error=str(e))


async def check_asr(config: RuntimeConfig) -> CheckResult:
    client = AsyncASRClient(
        app_key=config.asr_app_id,
        access_key=config.asr_access_token,
    )
    try:
        await client.init()

        async def empty_audio():
            yield b""

        async for _ in client.stream_asr(empty_audio()):
            break
        return CheckResult(ok=True, detail="ASR ok")
    except Exception as e:
        return CheckResult(ok=False, detail="ASR failed", error=str(e))
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def check_tts(config: RuntimeConfig) -> CheckResult:
    client = AsyncTTSClient(
        app_key=config.tts_app_id,
        access_key=config.tts_access_token,
        connection_params=ConnectionParams(
            speaker=config.tts_speaker,
            audio_params=AudioParams(),
        ),
    )
    try:
        await client.init()

        async def one_sentence():
            yield "你好"

        got_audio = False
        async for rsp in client.tts(source=one_sentence(), include_transcript=True):
            if rsp.audio:
                got_audio = True
            if rsp.event == EventSessionFinished:
                break
        if got_audio:
            return CheckResult(ok=True, detail="TTS ok")
        return CheckResult(
            ok=False,
            detail="TTS no audio",
            error="no audio from TTS",
        )
    except Exception as e:
        return CheckResult(ok=False, detail="TTS failed", error=str(e))
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def run_startup_self_check(
    config: Optional[RuntimeConfig] = None,
) -> SelfCheckReport:
    runtime = config or load_runtime_config()
    checks: Dict[str, CheckResult] = {}

    checks["llm"] = await check_llm(runtime)
    checks["asr"] = await check_asr(runtime)
    checks["tts"] = await check_tts(runtime)

    errors = {
        name: result.error
        for name, result in checks.items()
        if not result.ok and result.error
    }
    ok = all(result.ok for result in checks.values())
    return SelfCheckReport(ok=ok, checks=checks, errors=errors)


def format_self_check_lines(report: SelfCheckReport) -> List[str]:
    status = "PASS" if report.ok else "FAIL"
    lines = [f"[StartupSelfCheck] summary status={status}"]
    for name in ("llm", "asr", "tts"):
        result = report.checks.get(name)
        if not result:
            lines.append(f"[StartupSelfCheck] {name} status=FAIL detail=missing_result")
            continue
        detail = result.detail
        if result.error:
            detail = f"{detail} error={result.error}"
        lines.append(
            f"[StartupSelfCheck] {name} status={'PASS' if result.ok else 'FAIL'} detail={detail}"
        )
    return lines
