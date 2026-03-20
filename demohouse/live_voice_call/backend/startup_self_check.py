import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from arkitect.core.component.asr import AsyncASRClient
from arkitect.core.component.tts import AsyncTTSClient, AudioParams, ConnectionParams
from arkitect.core.component.tts.constants import EventSessionFinished

from ark_responses_adapter import ArkResponsesAdapter, normalize_stage_config

@dataclass(frozen=True)
class RuntimeConfig:
    ark_api_key: Optional[str]
    llm1_endpoint_id: Optional[str]
    llm2_endpoint_id: Optional[str]
    llm1_thinking_type: Optional[str]
    llm2_thinking_type: Optional[str]
    llm1_reasoning_effort: Optional[str]
    llm2_reasoning_effort: Optional[str]
    asr_app_id: Optional[str]
    asr_access_token: Optional[str]
    tts_app_id: Optional[str]
    tts_access_token: Optional[str]
    tts_speaker: Optional[str]


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


def _env(key: str) -> Optional[str]:
    value = os.environ.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        ark_api_key=_env("ARK_API_KEY"),
        llm1_endpoint_id=_env("LLM1_ENDPOINT_ID"),
        llm2_endpoint_id=_env("LLM2_ENDPOINT_ID"),
        llm1_thinking_type=_env("LLM1_THINKING_TYPE"),
        llm2_thinking_type=_env("LLM2_THINKING_TYPE"),
        llm1_reasoning_effort=_env("LLM1_REASONING_EFFORT"),
        llm2_reasoning_effort=_env("LLM2_REASONING_EFFORT"),
        asr_app_id=_env("ASR_APP_ID"),
        asr_access_token=_env("ASR_ACCESS_TOKEN"),
        tts_app_id=_env("TTS_APP_ID"),
        tts_access_token=_env("TTS_ACCESS_TOKEN"),
        tts_speaker=_env("TTS_SPEAKER"),
    )


async def _check_llm_stage(
    config: RuntimeConfig,
    *,
    stage_env_prefix: str,
    endpoint_id: Optional[str],
    thinking_type: Optional[str],
    reasoning_effort: Optional[str],
) -> CheckResult:
    if not config.ark_api_key:
        return CheckResult(
            ok=False,
            detail="ARK_API_KEY missing",
            error="missing ARK_API_KEY",
        )
    stage_config, normalize_error = normalize_stage_config(
        stage_name=stage_env_prefix,
        endpoint_id=endpoint_id,
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
    )
    if normalize_error:
        return CheckResult(
            ok=False,
            detail=f"{stage_env_prefix} config invalid",
            error=normalize_error,
        )
    if stage_config is None:
        return CheckResult(
            ok=False,
            detail=f"{stage_env_prefix} config invalid",
            error=f"internal error: normalized config missing for {stage_env_prefix}",
        )

    adapter = ArkResponsesAdapter(api_key=config.ark_api_key)
    try:
        text = await adapter.complete_text(
            model=stage_config.endpoint_id,
            instructions="你是一个中文助手。请用一句话回复用户。",
            messages=[{"role": "user", "content": "你好，回复一句话即可。"}],
            thinking_type=stage_config.thinking_type,
            reasoning_effort=stage_config.reasoning_effort,
        )
        if text.strip():
            return CheckResult(ok=True, detail=f"{stage_env_prefix} ok")
        return CheckResult(
            ok=False,
            detail=f"{stage_env_prefix} no content",
            error=f"no content from {stage_env_prefix}",
        )
    except Exception as e:
        return CheckResult(
            ok=False,
            detail=f"{stage_env_prefix} failed",
            error=str(e),
        )


async def check_llm1(config: RuntimeConfig) -> CheckResult:
    return await _check_llm_stage(
        config,
        stage_env_prefix="LLM1",
        endpoint_id=config.llm1_endpoint_id,
        thinking_type=config.llm1_thinking_type,
        reasoning_effort=config.llm1_reasoning_effort,
    )


async def check_llm2(config: RuntimeConfig) -> CheckResult:
    return await _check_llm_stage(
        config,
        stage_env_prefix="LLM2",
        endpoint_id=config.llm2_endpoint_id,
        thinking_type=config.llm2_thinking_type,
        reasoning_effort=config.llm2_reasoning_effort,
    )


async def check_asr(config: RuntimeConfig) -> CheckResult:
    if not config.asr_app_id:
        return CheckResult(
            ok=False,
            detail="ASR_APP_ID missing",
            error="missing ASR_APP_ID",
        )
    if not config.asr_access_token:
        return CheckResult(
            ok=False,
            detail="ASR_ACCESS_TOKEN missing",
            error="missing ASR_ACCESS_TOKEN",
        )

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
    if not config.tts_app_id:
        return CheckResult(
            ok=False,
            detail="TTS_APP_ID missing",
            error="missing TTS_APP_ID",
        )
    if not config.tts_access_token:
        return CheckResult(
            ok=False,
            detail="TTS_ACCESS_TOKEN missing",
            error="missing TTS_ACCESS_TOKEN",
        )
    if not config.tts_speaker:
        return CheckResult(
            ok=False,
            detail="TTS_SPEAKER missing",
            error="missing TTS_SPEAKER",
        )

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

    checks["llm1"] = await check_llm1(runtime)
    checks["llm2"] = await check_llm2(runtime)
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
    for name in ("llm1", "llm2", "asr", "tts"):
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
