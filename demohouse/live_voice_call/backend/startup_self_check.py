import asyncio
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from auc_stt_client import AucSTTClient
from arkitect.core.component.tts import AsyncTTSClient, AudioParams, ConnectionParams
from arkitect.core.component.tts.constants import EventSessionFinished

from ark_responses_adapter import (
    ArkResponsesAdapter,
    detect_responses_capability,
    normalize_stage_config,
)
from redis import Redis
from redis.exceptions import RedisError
from sauc_asr_client import DEFAULT_ASR_WS_URL, SaucASRClient

@dataclass(frozen=True)
class RuntimeConfig:
    ark_api_key: Optional[str]
    llm1_endpoint_id: Optional[str]
    llm2_endpoint_id: Optional[str]
    llm1_thinking_type: Optional[str]
    llm2_thinking_type: Optional[str]
    llm1_reasoning_effort: Optional[str]
    llm2_reasoning_effort: Optional[str]
    llm3_endpoint_id: Optional[str]
    llm3_thinking_type: Optional[str]
    llm3_reasoning_effort: Optional[str]
    asr_app_id: Optional[str]
    asr_access_token: Optional[str]
    asr_resource_id: Optional[str]
    asr_ws_url: Optional[str]
    tts_app_id: Optional[str]
    tts_access_token: Optional[str]
    tts_speaker: Optional[str]
    redis_url: Optional[str]
    stt_app_id: Optional[str] = None
    stt_access_token: Optional[str] = None
    stt_resource_id: Optional[str] = None
    stt_submit_url: Optional[str] = None
    stt_query_url: Optional[str] = None
    stt_task_timeout_ms: Optional[str] = None
    stt_poll_interval_ms: Optional[str] = None
    stt_audio_public_base_url: Optional[str] = None
    stt_audio_signing_secret: Optional[str] = None
    stt_audio_url_ttl_seconds: Optional[str] = None
    stt_self_check_audio_url: Optional[str] = None


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
        llm3_endpoint_id=_env("LLM3_ENDPOINT_ID"),
        llm3_thinking_type=_env("LLM3_THINKING_TYPE"),
        llm3_reasoning_effort=_env("LLM3_REASONING_EFFORT"),
        asr_app_id=_env("ASR_APP_ID"),
        asr_access_token=_env("ASR_ACCESS_TOKEN"),
        asr_resource_id=_env("ASR_RESOURCE_ID"),
        asr_ws_url=_env("ASR_WS_URL"),
        tts_app_id=_env("TTS_APP_ID"),
        tts_access_token=_env("TTS_ACCESS_TOKEN"),
        tts_speaker=_env("TTS_SPEAKER"),
        redis_url=_env("REDIS_URL"),
        stt_app_id=_env("STT_APP_ID"),
        stt_access_token=_env("STT_ACCESS_TOKEN"),
        stt_resource_id=_env("STT_RESOURCE_ID"),
        stt_submit_url=_env("STT_SUBMIT_URL"),
        stt_query_url=_env("STT_QUERY_URL"),
        stt_task_timeout_ms=_env("STT_TASK_TIMEOUT_MS"),
        stt_poll_interval_ms=_env("STT_POLL_INTERVAL_MS"),
        stt_audio_public_base_url=_env("STT_AUDIO_PUBLIC_BASE_URL"),
        stt_audio_signing_secret=_env("STT_AUDIO_SIGNING_SECRET"),
        stt_audio_url_ttl_seconds=_env("STT_AUDIO_URL_TTL_SECONDS"),
        stt_self_check_audio_url=_env("STT_SELF_CHECK_AUDIO_URL"),
    )


async def _check_llm_stage(
    config: RuntimeConfig,
    *,
    stage_env_prefix: str,
    endpoint_id: Optional[str],
    thinking_type: Optional[str],
    reasoning_effort: Optional[str],
) -> CheckResult:
    responses_ok, capability_detail = detect_responses_capability()
    if not responses_ok:
        return CheckResult(
            ok=False,
            detail=f"{stage_env_prefix} sdk unsupported",
            error=capability_detail,
        )

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


async def check_llm3(config: RuntimeConfig) -> CheckResult:
    if not config.llm3_endpoint_id:
        return CheckResult(ok=True, detail="LLM3 skipped (not configured)")
    return await _check_llm_stage(
        config,
        stage_env_prefix="LLM3",
        endpoint_id=config.llm3_endpoint_id,
        thinking_type=config.llm3_thinking_type,
        reasoning_effort=config.llm3_reasoning_effort,
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
    if not config.asr_resource_id:
        return CheckResult(
            ok=False,
            detail="ASR_RESOURCE_ID missing",
            error="missing ASR_RESOURCE_ID",
        )

    client = SaucASRClient(
        app_key=config.asr_app_id,
        access_key=config.asr_access_token,
        resource_id=config.asr_resource_id,
        ws_url=config.asr_ws_url or DEFAULT_ASR_WS_URL,
    )
    try:
        await client.init()

        async def probe_audio():
            # 100ms silence @16k/16bit/mono to probe stream path.
            yield b"\x00" * 3200

        async def _probe_stream() -> bool:
            async for _ in client.stream_asr(probe_audio()):
                return True
            return False

        got_response = await asyncio.wait_for(_probe_stream(), timeout=8)
        if not got_response:
            return CheckResult(
                ok=False,
                detail="ASR failed",
                error="ASR probe no response",
            )
        return CheckResult(ok=True, detail="ASR ok")
    except asyncio.TimeoutError:
        return CheckResult(ok=False, detail="ASR failed", error="ASR probe timeout")
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


async def check_redis(config: RuntimeConfig) -> CheckResult:
    if not config.redis_url:
        return CheckResult(
            ok=False,
            detail="REDIS_URL missing",
            error="missing REDIS_URL",
        )

    client = Redis.from_url(
        config.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        if not client.ping():
            return CheckResult(ok=False, detail="Redis failed", error="redis ping failed")

        maxmemory = client.config_get("maxmemory").get("maxmemory", "0")
        maxmemory_policy = client.config_get("maxmemory-policy").get("maxmemory-policy", "")
        expected_maxmemory = str(256 * 1024 * 1024)
        if str(maxmemory) != expected_maxmemory:
            return CheckResult(
                ok=False,
                detail="Redis config invalid",
                error=f"maxmemory should be {expected_maxmemory}, got {maxmemory}",
            )
        if (maxmemory_policy or "").strip().lower() != "allkeys-lru":
            return CheckResult(
                ok=False,
                detail="Redis config invalid",
                error=f"maxmemory-policy should be allkeys-lru, got {maxmemory_policy}",
            )
        return CheckResult(ok=True, detail="Redis ok")
    except RedisError as e:
        return CheckResult(ok=False, detail="Redis failed", error=str(e))
    finally:
        try:
            client.close()
        except Exception:
            pass


def _infer_audio_format_from_url(url: str) -> str:
    normalized = str(url or "").strip().lower()
    if normalized.endswith(".wav"):
        return "wav"
    if normalized.endswith(".mp3"):
        return "mp3"
    if normalized.endswith(".ogg"):
        return "ogg"
    return "raw"


async def check_stt(config: RuntimeConfig) -> CheckResult:
    provided_fields = [
        config.stt_app_id,
        config.stt_access_token,
        config.stt_resource_id,
        config.stt_audio_public_base_url,
        config.stt_audio_signing_secret,
        config.stt_submit_url,
        config.stt_query_url,
        config.stt_task_timeout_ms,
        config.stt_poll_interval_ms,
        config.stt_audio_url_ttl_seconds,
    ]
    if not any(str(item or "").strip() for item in provided_fields):
        return CheckResult(ok=True, detail="STT skipped (not configured)")

    missing = []
    if not config.stt_app_id:
        missing.append("STT_APP_ID")
    if not config.stt_access_token:
        missing.append("STT_ACCESS_TOKEN")
    if not config.stt_resource_id:
        missing.append("STT_RESOURCE_ID")
    if not config.stt_audio_public_base_url:
        missing.append("STT_AUDIO_PUBLIC_BASE_URL")
    if not config.stt_audio_signing_secret:
        missing.append("STT_AUDIO_SIGNING_SECRET")
    if missing:
        return CheckResult(
            ok=False,
            detail="STT config invalid",
            error=f"missing {','.join(missing)}",
        )

    probe_url = str(config.stt_self_check_audio_url or "").strip()
    if not probe_url:
        return CheckResult(ok=True, detail="STT config ok (probe skipped)")

    try:
        task_timeout_ms = int(str(config.stt_task_timeout_ms or "90000"))
    except ValueError:
        task_timeout_ms = 90000
    try:
        poll_interval_ms = int(str(config.stt_poll_interval_ms or "1000"))
    except ValueError:
        poll_interval_ms = 1000

    client = AucSTTClient(
        app_id=str(config.stt_app_id or ""),
        access_token=str(config.stt_access_token or ""),
        resource_id=str(config.stt_resource_id or ""),
        submit_url=str(config.stt_submit_url or ""),
        query_url=str(config.stt_query_url or ""),
        task_timeout_ms=max(5000, task_timeout_ms),
        poll_interval_ms=max(200, poll_interval_ms),
    )
    try:
        result = await asyncio.to_thread(
            client.transcribe_audio_url,
            audio_url=probe_url,
            audio_format=_infer_audio_format_from_url(probe_url),
            show_utterances=True,
        )
    except Exception as e:
        return CheckResult(ok=False, detail="STT failed", error=str(e))

    if str(result.text or "").strip():
        return CheckResult(ok=True, detail="STT ok")
    return CheckResult(ok=False, detail="STT failed", error="STT probe empty result")


async def run_startup_self_check(
    config: Optional[RuntimeConfig] = None,
) -> SelfCheckReport:
    runtime = config or load_runtime_config()
    checks: Dict[str, CheckResult] = {}

    checks["llm1"] = await check_llm1(runtime)
    checks["llm2"] = await check_llm2(runtime)
    checks["llm3"] = await check_llm3(runtime)
    checks["asr"] = await check_asr(runtime)
    checks["tts"] = await check_tts(runtime)
    checks["redis"] = await check_redis(runtime)
    checks["stt"] = await check_stt(runtime)

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
    for name in ("llm1", "llm2", "llm3", "asr", "tts", "redis", "stt"):
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
