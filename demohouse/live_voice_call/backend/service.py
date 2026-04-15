# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# Licensed under the 【火山方舟】原型应用软件自用许可协议
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at 
#     https://www.volcengine.com/docs/82379/1433703
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License. 

import asyncio
import contextlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterable, Callable, Dict, List, Optional, Union

from arkitect.core.component.llm import BaseChatLanguageModel
from arkitect.core.component.llm.model import ArkMessage
from arkitect.core.component.tts import AsyncTTSClient, AudioParams, ConnectionParams
from arkitect.core.component.tts.constants import (
    EventSessionFinished,
    EventTTSSentenceEnd,
    EventTTSSentenceStart,
)
from arkitect.telemetry.logger import INFO
from event import *
from interview_flow import (
    ASK_CLARIFY,
    ASK_FOLLOWUP,
    ASK_QUESTION,
    DONE,
    INTRO,
    QuestionAnswerSnapshot,
    WAIT_ANSWER,
    WRAP_UP,
    FlowResponse,
    InterviewFlow,
)
from interview_judge import Decision, InterviewJudge
from prompt import INTERVIEWER_SYSTEM_PROMPT, VoiceBotPrompt
from llm_limiter import llm_slot
from ark_responses_adapter import ArkResponsesAdapter, build_input_messages
from sauc_asr_client import DEFAULT_ASR_WS_URL, SaucASRClient, SaucASRFullServerResponse
from scripted_tts_memory_cache import (
    SCRIPTED_TTS_MEMORY_CACHE,
    CachedTTSSentence,
    ScriptedTTSMemoryCache,
)

StateInProgress = "InProgress"
StateIdle = "Idle"
# Keep local turn-finalize timeout below upstream ASR "wait next packet" timeout
# to avoid race conditions that can surface as server-side timeout errors.
DEFAULT_ASR_SILENCE_TIMEOUT_MS = 8000
MAX_ASR_SILENCE_TIMEOUT_MS = 15000
DEFAULT_ASR_PRE_FINALIZE_GRACE_MS = 400
MAX_ASR_PRE_FINALIZE_GRACE_MS = 2000
DEFAULT_ASR_MANUAL_TURN_END_ENABLED = False
DEFAULT_ASR_SEGMENTATION_MODE = "semantic"
DEFAULT_ASR_AIVAD = True
DEFAULT_ASR_SILENCE_TIME_MS = 800
MIN_ASR_SILENCE_TIME_MS = 500
MAX_ASR_SILENCE_TIME_MS = 3000
DEFAULT_ASR_END_WINDOW_SIZE_MS = 800
MIN_ASR_END_WINDOW_SIZE_MS = 300
MAX_ASR_END_WINDOW_SIZE_MS = 5000
DEFAULT_ASR_USE_UTTERANCES = True
DEFAULT_ASR_STABLE_PREFIX_PACKETS = 3
MIN_ASR_STABLE_PREFIX_PACKETS = 2
MAX_ASR_STABLE_PREFIX_PACKETS = 5
DEFAULT_ASR_CLIENT_END_STABILIZE_MS = 300
MAX_ASR_CLIENT_END_STABILIZE_MS = 800
DEFAULT_ASR_ENABLE_PREFIX_NO_REWIND = True
DEFAULT_ASR_ALIGN_TAIL_WINDOW_TOKENS = 80
MIN_ASR_ALIGN_TAIL_WINDOW_TOKENS = 40
MAX_ASR_ALIGN_TAIL_WINDOW_TOKENS = 160
DEFAULT_ASR_ALIGN_HEAD_WINDOW_TOKENS = 160
MIN_ASR_ALIGN_HEAD_WINDOW_TOKENS = 80
MAX_ASR_ALIGN_HEAD_WINDOW_TOKENS = 320
DEFAULT_ASR_ALIGN_MIN_SCORE = 8.0
MIN_ASR_ALIGN_MIN_SCORE = 0.0
MAX_ASR_ALIGN_MIN_SCORE = 30.0
DEFAULT_ASR_ALIGN_MIN_MATCH_RATIO = 0.55
MIN_ASR_ALIGN_MIN_MATCH_RATIO = 0.3
MAX_ASR_ALIGN_MIN_MATCH_RATIO = 0.9
ASR_REWRITE_QUALITY_TOLERANCE = 1.0
DEFAULT_INTERVIEW_GLOBAL_TURN_LIMIT = 300
RESUME_MODE_NONE = "none"
RESUME_MODE_QUESTION_START = "question_start"
RESUME_MODE_WRAP_UP = "wrap_up"
RESUME_MODE_DONE = "done"
RESUME_FIRST_TURN_GUARD_REMIND_TEXT = (
    "我听到了，请继续围绕刚才这道题补充更完整的回答，"
    "可以从你的具体做法、关键判断和结果展开。"
)


def _load_asr_silence_timeout_ms() -> int:
    raw_value = (os.getenv("ASR_SILENCE_TIMEOUT_MS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_SILENCE_TIMEOUT_MS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_SILENCE_TIMEOUT_MS invalid, fallback to default "
            f"value={DEFAULT_ASR_SILENCE_TIMEOUT_MS}"
        )
        return DEFAULT_ASR_SILENCE_TIMEOUT_MS
    if parsed <= 0:
        INFO(
            "ASR_SILENCE_TIMEOUT_MS must be positive, fallback to default "
            f"value={DEFAULT_ASR_SILENCE_TIMEOUT_MS}"
        )
        return DEFAULT_ASR_SILENCE_TIMEOUT_MS
    if parsed > MAX_ASR_SILENCE_TIMEOUT_MS:
        INFO(
            "ASR_SILENCE_TIMEOUT_MS too large, clamp to safe max "
            f"value={MAX_ASR_SILENCE_TIMEOUT_MS}"
        )
        return MAX_ASR_SILENCE_TIMEOUT_MS
    return parsed


def _load_asr_pre_finalize_grace_ms() -> int:
    raw_value = (os.getenv("ASR_PRE_FINALIZE_GRACE_MS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_PRE_FINALIZE_GRACE_MS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_PRE_FINALIZE_GRACE_MS invalid, fallback to default "
            f"value={DEFAULT_ASR_PRE_FINALIZE_GRACE_MS}"
        )
        return DEFAULT_ASR_PRE_FINALIZE_GRACE_MS
    if parsed < 0:
        INFO(
            "ASR_PRE_FINALIZE_GRACE_MS must be non-negative, fallback to default "
            f"value={DEFAULT_ASR_PRE_FINALIZE_GRACE_MS}"
        )
        return DEFAULT_ASR_PRE_FINALIZE_GRACE_MS
    if parsed > MAX_ASR_PRE_FINALIZE_GRACE_MS:
        INFO(
            "ASR_PRE_FINALIZE_GRACE_MS too large, clamp to safe max "
            f"value={MAX_ASR_PRE_FINALIZE_GRACE_MS}"
        )
        return MAX_ASR_PRE_FINALIZE_GRACE_MS
    return parsed


def _load_asr_manual_turn_end_enabled() -> bool:
    raw_value = (os.getenv("ASR_MANUAL_TURN_END_ENABLED") or "").strip().lower()
    if not raw_value:
        return DEFAULT_ASR_MANUAL_TURN_END_ENABLED
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    INFO(
        "ASR_MANUAL_TURN_END_ENABLED invalid, fallback to default "
        f"value={str(DEFAULT_ASR_MANUAL_TURN_END_ENABLED).lower()}"
    )
    return DEFAULT_ASR_MANUAL_TURN_END_ENABLED


def _parse_bool_env(raw_value: str, default: bool, *, name: str) -> bool:
    value = (raw_value or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    INFO(f"{name} invalid, fallback to default value={str(default).lower()}")
    return default


def _load_asr_segmentation_mode() -> str:
    raw_value = (os.getenv("ASR_SEGMENTATION_MODE") or "").strip().lower()
    if not raw_value:
        return DEFAULT_ASR_SEGMENTATION_MODE
    if raw_value in {"semantic", "silence"}:
        return raw_value
    INFO(
        "ASR_SEGMENTATION_MODE invalid, fallback to default "
        f"value={DEFAULT_ASR_SEGMENTATION_MODE}"
    )
    return DEFAULT_ASR_SEGMENTATION_MODE


def _load_asr_aivad_enabled() -> bool:
    return _parse_bool_env(
        os.getenv("ASR_AIVAD") or "",
        DEFAULT_ASR_AIVAD,
        name="ASR_AIVAD",
    )


def _load_asr_silence_time_ms() -> int:
    raw_value = (os.getenv("ASR_SILENCE_TIME_MS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_SILENCE_TIME_MS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_SILENCE_TIME_MS invalid, fallback to default "
            f"value={DEFAULT_ASR_SILENCE_TIME_MS}"
        )
        return DEFAULT_ASR_SILENCE_TIME_MS
    if parsed < MIN_ASR_SILENCE_TIME_MS:
        INFO(
            "ASR_SILENCE_TIME_MS too small, clamp to min "
            f"value={MIN_ASR_SILENCE_TIME_MS}"
        )
        return MIN_ASR_SILENCE_TIME_MS
    if parsed > MAX_ASR_SILENCE_TIME_MS:
        INFO(
            "ASR_SILENCE_TIME_MS too large, clamp to max "
            f"value={MAX_ASR_SILENCE_TIME_MS}"
        )
        return MAX_ASR_SILENCE_TIME_MS
    return parsed


def _load_asr_end_window_size_ms() -> int:
    raw_value = (os.getenv("ASR_END_WINDOW_SIZE_MS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_END_WINDOW_SIZE_MS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_END_WINDOW_SIZE_MS invalid, fallback to default "
            f"value={DEFAULT_ASR_END_WINDOW_SIZE_MS}"
        )
        return DEFAULT_ASR_END_WINDOW_SIZE_MS
    if parsed < MIN_ASR_END_WINDOW_SIZE_MS:
        INFO(
            "ASR_END_WINDOW_SIZE_MS too small, clamp to min "
            f"value={MIN_ASR_END_WINDOW_SIZE_MS}"
        )
        return MIN_ASR_END_WINDOW_SIZE_MS
    if parsed > MAX_ASR_END_WINDOW_SIZE_MS:
        INFO(
            "ASR_END_WINDOW_SIZE_MS too large, clamp to max "
            f"value={MAX_ASR_END_WINDOW_SIZE_MS}"
        )
        return MAX_ASR_END_WINDOW_SIZE_MS
    return parsed


def _load_asr_use_utterances() -> bool:
    return _parse_bool_env(
        os.getenv("ASR_USE_UTTERANCES") or "",
        DEFAULT_ASR_USE_UTTERANCES,
        name="ASR_USE_UTTERANCES",
    )


def _load_asr_stable_prefix_packets() -> int:
    raw_value = (os.getenv("ASR_STABLE_PREFIX_PACKETS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_STABLE_PREFIX_PACKETS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_STABLE_PREFIX_PACKETS invalid, fallback to default "
            f"value={DEFAULT_ASR_STABLE_PREFIX_PACKETS}"
        )
        return DEFAULT_ASR_STABLE_PREFIX_PACKETS
    if parsed < MIN_ASR_STABLE_PREFIX_PACKETS:
        INFO(
            "ASR_STABLE_PREFIX_PACKETS too small, clamp to min "
            f"value={MIN_ASR_STABLE_PREFIX_PACKETS}"
        )
        return MIN_ASR_STABLE_PREFIX_PACKETS
    if parsed > MAX_ASR_STABLE_PREFIX_PACKETS:
        INFO(
            "ASR_STABLE_PREFIX_PACKETS too large, clamp to max "
            f"value={MAX_ASR_STABLE_PREFIX_PACKETS}"
        )
        return MAX_ASR_STABLE_PREFIX_PACKETS
    return parsed


def _load_asr_client_end_stabilize_ms() -> int:
    raw_value = (os.getenv("ASR_CLIENT_END_STABILIZE_MS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_CLIENT_END_STABILIZE_MS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_CLIENT_END_STABILIZE_MS invalid, fallback to default "
            f"value={DEFAULT_ASR_CLIENT_END_STABILIZE_MS}"
        )
        return DEFAULT_ASR_CLIENT_END_STABILIZE_MS
    if parsed < 0:
        INFO(
            "ASR_CLIENT_END_STABILIZE_MS must be non-negative, fallback to default "
            f"value={DEFAULT_ASR_CLIENT_END_STABILIZE_MS}"
        )
        return DEFAULT_ASR_CLIENT_END_STABILIZE_MS
    if parsed > MAX_ASR_CLIENT_END_STABILIZE_MS:
        INFO(
            "ASR_CLIENT_END_STABILIZE_MS too large, clamp to max "
            f"value={MAX_ASR_CLIENT_END_STABILIZE_MS}"
        )
        return MAX_ASR_CLIENT_END_STABILIZE_MS
    return parsed


def _load_asr_enable_prefix_no_rewind() -> bool:
    return _parse_bool_env(
        os.getenv("ASR_ENABLE_PREFIX_NO_REWIND") or "",
        DEFAULT_ASR_ENABLE_PREFIX_NO_REWIND,
        name="ASR_ENABLE_PREFIX_NO_REWIND",
    )


def _load_asr_align_tail_window_tokens() -> int:
    raw_value = (os.getenv("ASR_ALIGN_TAIL_WINDOW_TOKENS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_ALIGN_TAIL_WINDOW_TOKENS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_ALIGN_TAIL_WINDOW_TOKENS invalid, fallback to default "
            f"value={DEFAULT_ASR_ALIGN_TAIL_WINDOW_TOKENS}"
        )
        return DEFAULT_ASR_ALIGN_TAIL_WINDOW_TOKENS
    if parsed < MIN_ASR_ALIGN_TAIL_WINDOW_TOKENS:
        INFO(
            "ASR_ALIGN_TAIL_WINDOW_TOKENS too small, clamp to min "
            f"value={MIN_ASR_ALIGN_TAIL_WINDOW_TOKENS}"
        )
        return MIN_ASR_ALIGN_TAIL_WINDOW_TOKENS
    if parsed > MAX_ASR_ALIGN_TAIL_WINDOW_TOKENS:
        INFO(
            "ASR_ALIGN_TAIL_WINDOW_TOKENS too large, clamp to max "
            f"value={MAX_ASR_ALIGN_TAIL_WINDOW_TOKENS}"
        )
        return MAX_ASR_ALIGN_TAIL_WINDOW_TOKENS
    return parsed


def _load_asr_align_head_window_tokens() -> int:
    raw_value = (os.getenv("ASR_ALIGN_HEAD_WINDOW_TOKENS") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_ALIGN_HEAD_WINDOW_TOKENS
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "ASR_ALIGN_HEAD_WINDOW_TOKENS invalid, fallback to default "
            f"value={DEFAULT_ASR_ALIGN_HEAD_WINDOW_TOKENS}"
        )
        return DEFAULT_ASR_ALIGN_HEAD_WINDOW_TOKENS
    if parsed < MIN_ASR_ALIGN_HEAD_WINDOW_TOKENS:
        INFO(
            "ASR_ALIGN_HEAD_WINDOW_TOKENS too small, clamp to min "
            f"value={MIN_ASR_ALIGN_HEAD_WINDOW_TOKENS}"
        )
        return MIN_ASR_ALIGN_HEAD_WINDOW_TOKENS
    if parsed > MAX_ASR_ALIGN_HEAD_WINDOW_TOKENS:
        INFO(
            "ASR_ALIGN_HEAD_WINDOW_TOKENS too large, clamp to max "
            f"value={MAX_ASR_ALIGN_HEAD_WINDOW_TOKENS}"
        )
        return MAX_ASR_ALIGN_HEAD_WINDOW_TOKENS
    return parsed


def _load_asr_align_min_score() -> float:
    raw_value = (os.getenv("ASR_ALIGN_MIN_SCORE") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_ALIGN_MIN_SCORE
    try:
        parsed = float(raw_value)
    except ValueError:
        INFO(
            "ASR_ALIGN_MIN_SCORE invalid, fallback to default "
            f"value={DEFAULT_ASR_ALIGN_MIN_SCORE}"
        )
        return DEFAULT_ASR_ALIGN_MIN_SCORE
    if parsed < MIN_ASR_ALIGN_MIN_SCORE:
        INFO(
            "ASR_ALIGN_MIN_SCORE too small, clamp to min "
            f"value={MIN_ASR_ALIGN_MIN_SCORE}"
        )
        return MIN_ASR_ALIGN_MIN_SCORE
    if parsed > MAX_ASR_ALIGN_MIN_SCORE:
        INFO(
            "ASR_ALIGN_MIN_SCORE too large, clamp to max "
            f"value={MAX_ASR_ALIGN_MIN_SCORE}"
        )
        return MAX_ASR_ALIGN_MIN_SCORE
    return parsed


def _load_asr_align_min_match_ratio() -> float:
    raw_value = (os.getenv("ASR_ALIGN_MIN_MATCH_RATIO") or "").strip()
    if not raw_value:
        return DEFAULT_ASR_ALIGN_MIN_MATCH_RATIO
    try:
        parsed = float(raw_value)
    except ValueError:
        INFO(
            "ASR_ALIGN_MIN_MATCH_RATIO invalid, fallback to default "
            f"value={DEFAULT_ASR_ALIGN_MIN_MATCH_RATIO}"
        )
        return DEFAULT_ASR_ALIGN_MIN_MATCH_RATIO
    if parsed < MIN_ASR_ALIGN_MIN_MATCH_RATIO:
        INFO(
            "ASR_ALIGN_MIN_MATCH_RATIO too small, clamp to min "
            f"value={MIN_ASR_ALIGN_MIN_MATCH_RATIO}"
        )
        return MIN_ASR_ALIGN_MIN_MATCH_RATIO
    if parsed > MAX_ASR_ALIGN_MIN_MATCH_RATIO:
        INFO(
            "ASR_ALIGN_MIN_MATCH_RATIO too large, clamp to max "
            f"value={MAX_ASR_ALIGN_MIN_MATCH_RATIO}"
        )
        return MAX_ASR_ALIGN_MIN_MATCH_RATIO
    return parsed


def _load_interview_global_turn_limit() -> int:
    raw_value = (os.getenv("INTERVIEW_GLOBAL_TURN_LIMIT") or "").strip()
    if not raw_value:
        return DEFAULT_INTERVIEW_GLOBAL_TURN_LIMIT
    try:
        parsed = int(raw_value)
    except ValueError:
        INFO(
            "INTERVIEW_GLOBAL_TURN_LIMIT invalid, fallback to default "
            f"value={DEFAULT_INTERVIEW_GLOBAL_TURN_LIMIT}"
        )
        return DEFAULT_INTERVIEW_GLOBAL_TURN_LIMIT
    if parsed <= 0:
        INFO(
            "INTERVIEW_GLOBAL_TURN_LIMIT must be positive, fallback to default "
            f"value={DEFAULT_INTERVIEW_GLOBAL_TURN_LIMIT}"
        )
        return DEFAULT_INTERVIEW_GLOBAL_TURN_LIMIT
    return parsed


# asr continuous detection no input duration, configurable via env
ASRInterval = _load_asr_silence_timeout_ms()
ASR_PRE_FINALIZE_GRACE_MS = _load_asr_pre_finalize_grace_ms()
ASR_MANUAL_TURN_END_ENABLED = _load_asr_manual_turn_end_enabled()
ASR_SEGMENTATION_MODE = _load_asr_segmentation_mode()
ASR_AIVAD = _load_asr_aivad_enabled()
ASR_SILENCE_TIME_MS = _load_asr_silence_time_ms()
ASR_END_WINDOW_SIZE_MS = _load_asr_end_window_size_ms()
ASR_USE_UTTERANCES = _load_asr_use_utterances()
ASR_STABLE_PREFIX_PACKETS = _load_asr_stable_prefix_packets()
ASR_CLIENT_END_STABILIZE_MS = _load_asr_client_end_stabilize_ms()
ASR_ENABLE_PREFIX_NO_REWIND = _load_asr_enable_prefix_no_rewind()
ASR_ALIGN_TAIL_WINDOW_TOKENS = _load_asr_align_tail_window_tokens()
ASR_ALIGN_HEAD_WINDOW_TOKENS = _load_asr_align_head_window_tokens()
ASR_ALIGN_MIN_SCORE = _load_asr_align_min_score()
ASR_ALIGN_MIN_MATCH_RATIO = _load_asr_align_min_match_ratio()
ASR_POLL_INTERVAL_SECONDS = 0.2
ASR_SILENCE_LOG_EVERY_TICKS = 10
ASR_INIT_TIMEOUT_SECONDS = 12
ASR_INIT_MAX_ATTEMPTS = 2
ASR_INIT_RETRY_BACKOFF_SECONDS = 0.2
ASR_INIT_FATAL_FAILURE_STREAK = 3
# Default TTS speaker. Prefer environment override to avoid code-level hardcoding.
DEFAULT_SPEAKER = (
    (os.getenv("TTS_SPEAKER") or "").strip() or "zh_female_sajiaonvyou_moon_bigtts"
)
# Greeting message spoken by the bot before the user starts
GREETING_TEXT = "你好，欢迎参加今天的面试。请先做一个简短的自我介绍吧。"

DEFAULT_INTERVIEW_QUESTIONS: List[Dict[str, Any]] = [
    {
        "question_id": "q1",
        "main_question": "请用1分钟做自我介绍，重点说与你申请岗位相关的经历。",
        "evidence": {"scoring_boundary": "是否清晰说明岗位相关经历，并体现与岗位的匹配度"},
    },
    {
        "question_id": "q2",
        "main_question": "请介绍一个你主导的项目，说明目标、你的动作和结果。",
        "evidence": {"scoring_boundary": "是否完整覆盖项目目标、个人关键动作和可验证结果"},
    },
    {
        "question_id": "q3",
        "main_question": "当你遇到复杂问题时，通常如何拆解并推动解决？",
        "evidence": {"scoring_boundary": "是否体现问题拆解方法、推进机制和复盘意识"},
    },
]


class ASRInitUnavailableError(RuntimeError):
    """Raised when ASR init keeps failing and should be surfaced to frontend."""


class VoiceBotService(BaseModel):
    asr_client: Optional[Any] = None
    tts_client: Optional[AsyncTTSClient] = None
    ark_api_key: str
    llm1_endpoint_id: str
    llm2_endpoint_id: str
    llm1_thinking_type: str = "disabled"
    llm2_thinking_type: str = "disabled"
    llm1_reasoning_effort: Optional[str] = None
    llm2_reasoning_effort: Optional[str] = None
    state: str = StateIdle
    tts_speaker: str = DEFAULT_SPEAKER  # TTS live_voice_call

    """
    config vars
    """
    asr_app_key: str
    asr_access_key: str
    asr_resource_id: str = (os.getenv("ASR_RESOURCE_ID") or "").strip()
    asr_ws_url: str = (os.getenv("ASR_WS_URL") or "").strip() or DEFAULT_ASR_WS_URL
    tts_app_key: str
    tts_access_key: str

    interview_mode: bool = False
    interview_flow: Optional[Any] = None
    interview_judge: Optional[Any] = None
    interview_questions: Optional[List[Dict[str, Any]]] = None
    interview_runtime_checkpoint: Optional[Dict[str, Any]] = None
    on_candidate_sentence: Optional[Callable[[str], None]] = None
    on_bot_sentence: Optional[Callable[[str], None]] = None
    on_bot_audio_chunk: Optional[Callable[[bytes], None]] = None
    on_interview_completed: Optional[Callable[[], None]] = None
    on_interview_runtime_checkpoint: Optional[Callable[[Dict[str, Any]], None]] = None
    log_fn: Optional[Callable[[str], None]] = None
    session_id: str = ""
    responses_adapter: Optional[Any] = None
    interview_resumed_from_checkpoint: bool = False
    interview_resume_mode: str = RESUME_MODE_NONE
    interview_resume_low_signal_guard_active: bool = False
    interview_token: str = ""
    scripted_tts_cache: Optional[ScriptedTTSMemoryCache] = None

    history_messages: List[ArkMessage] = []  # Store historical dialogue information
    segment_history_messages: Dict[str, List[ArkMessage]] = {}
    question_context_segments: Dict[str, str] = {}
    active_context_segment: str = ""

    asr_buffer: str = ""  # Reservoir asr recognition result
    asr_snapshot_text: str = ""
    asr_committed_text: str = ""
    asr_draft_text: str = ""
    asr_recent_snapshots: List[str] = []
    asr_no_input_duration: int = 0  # Cumulated no live_voice_call recognition duration
    asr_last_duration: int = 0  # Last asr recognition duration
    asr_last_growth_mono_ms: int = 0  # Last monotonic ts when ASR text changed
    asr_silence_tick_count: int = 0  # Tick counter for sampled silence logs
    asr_last_stream_end_reason: str = ""  # Last observed ASR stream end reason
    asr_stream_reset_count: int = 0  # Number of ASR stream reset attempts
    asr_stream_last_reset_mono_ms: int = 0  # Last ASR stream reset monotonic ts
    asr_init_count: int = 0  # Count actual asr_client.init() calls per service instance
    asr_init_failure_streak: int = 0  # Consecutive init failures since last success
    asr_force_finalize_requested: bool = False
    asr_force_finalize_requested_mono_ms: int = 0
    asr_client_end_best_sentence: str = ""
    asr_client_end_best_score: float = 0.0
    asr_client_end_best_committed_len: int = 0
    asr_committed_end_time_ms: int = 0
    asr_last_partial_sentence: str = ""
    asr_stale_connect_id: str = ""
    asr_drop_stale_packets: bool = False
    emit_asr_partial_events: bool = False
    current_turn_id: Optional[str] = None
    turn_timestamps_ms: Optional[Dict[str, int]] = None

    class Config:
        """Configuration for this pydantic object."""

        arbitrary_types_allowed = True

    async def init(self):
        """
        Initialize the TTS and ASR clients.
        """
        self.tts_client = AsyncTTSClient(
            app_key=self.tts_app_key,
            access_key=self.tts_access_key,
            connection_params=ConnectionParams(
                speaker=self.tts_speaker, audio_params=AudioParams()
            ),
        )
        self.asr_client = SaucASRClient(
            app_key=self.asr_app_key,
            access_key=self.asr_access_key,
            resource_id=self.asr_resource_id,
            ws_url=self.asr_ws_url,
            segmentation_mode=ASR_SEGMENTATION_MODE,
            aivad_enabled=ASR_AIVAD,
            silence_time_ms=ASR_SILENCE_TIME_MS,
            end_window_size_ms=ASR_END_WINDOW_SIZE_MS,
            log_fn=self._log,
        )
        # await self.tts_client.init() # 这里也不需要，因为 lazy init 会按需建立连接。
        # await self.asr_client.init() # 这里有问题，需要comment 掉才可以正常使用。因为是 AI 先说话，这时初始化 client 会导致 server 挂起。


        if self.interview_mode:
            self.emit_asr_partial_events = True
            if not self.responses_adapter:
                self.responses_adapter = ArkResponsesAdapter(api_key=self.ark_api_key)
            flow_questions = self.interview_questions or DEFAULT_INTERVIEW_QUESTIONS
            self.interview_judge = InterviewJudge(
                llm_endpoint_id=self.llm1_endpoint_id,
                llm_thinking_type=self.llm1_thinking_type,
                llm_reasoning_effort=self.llm1_reasoning_effort,
                responses_adapter=self.responses_adapter,
                coverage_threshold=0.7,
            )
            self.interview_flow = InterviewFlow(
                questions=flow_questions,
                judge=self.interview_judge,
                global_turn_limit=_load_interview_global_turn_limit(),
            )
            self._build_question_context_segments(flow_questions)
            self.interview_resume_mode = RESUME_MODE_NONE
            self.interview_resumed_from_checkpoint = False
            self.interview_resume_low_signal_guard_active = False
            runtime_checkpoint = self.interview_runtime_checkpoint
            if isinstance(runtime_checkpoint, dict):
                restored = self.interview_flow.restore_runtime_checkpoint(
                    runtime_checkpoint
                )
                if restored:
                    rewound = False
                    if self.interview_flow.state == DONE:
                        self.interview_resume_mode = RESUME_MODE_DONE
                    elif self.interview_flow.state == WRAP_UP:
                        self.interview_resume_mode = RESUME_MODE_WRAP_UP
                    else:
                        rewound = self.interview_flow.rewind_to_question_start(
                            self.interview_flow.current_question_index
                        )
                        if rewound:
                            self.interview_resume_mode = RESUME_MODE_QUESTION_START
                            self.interview_resumed_from_checkpoint = True
                            self.interview_resume_low_signal_guard_active = True
                            self._emit_interview_runtime_checkpoint()
                    self._log(
                        "[Interview] Runtime checkpoint restored "
                        f"restored={restored} rewound={rewound} "
                        f"resume_mode={self.interview_resume_mode} "
                        f"question_index={self.interview_flow.current_question_index}"
                    )

    def _log(self, message: str) -> None:
        if self.log_fn:
            try:
                self.log_fn(message)
                return
            except Exception as log_err:
                INFO(f"[VoiceBotService] custom log_fn failed: {log_err}")
        INFO(message)

    def _build_question_context_segments(self, questions: List[Dict[str, Any]]) -> None:
        self.question_context_segments = {}
        self.segment_history_messages = {}
        self.active_context_segment = ""
        previous_scene = ""
        current_scene_segment = ""

        for idx, item in enumerate(questions):
            question_id = str(item.get("question_id", "") or "").strip() or f"q{idx + 1}"
            evidence = item.get("evidence", {})
            scenario = str(item.get("scenario", "") or "").strip()
            if not scenario and isinstance(evidence, dict):
                scenario = str(evidence.get("scenario", "") or "").strip()

            if scenario:
                if scenario == previous_scene and current_scene_segment:
                    segment_id = current_scene_segment
                else:
                    segment_id = f"scene:{idx}:{scenario}"
                    current_scene_segment = segment_id
            else:
                segment_id = f"single:{idx}:{question_id}"
                current_scene_segment = segment_id

            self.question_context_segments[question_id] = segment_id
            self.segment_history_messages.setdefault(segment_id, [])
            previous_scene = scenario

    def _activate_context_segment(self, question_id: Optional[str]) -> None:
        if not question_id:
            return
        segment_id = self.question_context_segments.get(str(question_id))
        if not segment_id:
            return
        self.active_context_segment = segment_id
        self.segment_history_messages.setdefault(segment_id, [])

    def _history_for_current_segment(self) -> List[ArkMessage]:
        segment_id = self.active_context_segment.strip()
        if not segment_id:
            return self.history_messages
        return self.segment_history_messages.get(segment_id, [])

    def _append_history_message(self, role: str, content: str) -> None:
        message = ArkMessage(**{"role": role, "content": content})
        self.history_messages.append(message)
        segment_id = self.active_context_segment.strip()
        if segment_id:
            self.segment_history_messages.setdefault(segment_id, []).append(message)

    def _sanitize_initial_question_text(self, text: str) -> str:
        """Remove opening transition phrases for the very first question."""
        original = (text or "").strip()
        if not original:
            return ""
        sanitized = original
        prefix_patterns = [
            r"^(?:好的|好)[，,\s]*我们?(?:先)?(?:进入下一题|进入下一个场景)[，,。；;：:\s]*",
            r"^(?:好的|好)[，,\s]*我们?(?:先)?继续(?:下一题|下一个问题|下一个场景)?[，,。；;：:\s]*",
            r"^我们(?:先)?(?:进入下一题|进入下一个场景|继续(?:下一题|下一个问题|下一个场景)?)[，,。；;：:\s]*",
        ]
        for _ in range(2):
            before = sanitized
            for pattern in prefix_patterns:
                sanitized = re.sub(pattern, "", sanitized, count=1).lstrip()
            if sanitized == before:
                break
        return sanitized or original

    def _normalize_candidate_answer_for_guard(self, answer: str) -> str:
        normalized = re.sub(r"[，。！？、；：,.!?;:\-\s~…]+", "", str(answer or ""))
        return normalized.strip().lower()

    def _is_low_signal_candidate_answer(self, answer: str) -> bool:
        normalized = self._normalize_candidate_answer_for_guard(answer)
        if not normalized:
            return True
        if len(normalized) > 6:
            return False

        filler_tokens = {
            "嗯",
            "嗯嗯",
            "啊",
            "啊啊",
            "呃",
            "额",
            "哦",
            "噢",
            "唔",
            "哎",
            "诶",
            "欸",
            "哈",
            "然后",
            "就是",
            "那个",
            "这个",
            "好的",
            "好吧",
            "嗯哼",
            "uh",
            "um",
            "hmm",
            "hm",
        }
        if normalized in filler_tokens:
            return True
        return bool(
            re.fullmatch(
                r"(?:嗯+|啊+|呃+|额+|哦+|噢+|唔+|哎+|诶+|欸+|哈+|uh+|um+|hmm+|hm+)",
                normalized,
            )
        )

    def _wall_time_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _mono_ms(self) -> int:
        return int(round(time.monotonic() * 1000))

    def _reset_asr_buffer_state(self) -> None:
        self.asr_buffer = ""
        self.asr_snapshot_text = ""
        self.asr_committed_text = ""
        self.asr_draft_text = ""
        self.asr_recent_snapshots = []
        self.asr_no_input_duration = 0
        self.asr_last_duration = 0
        self.asr_last_growth_mono_ms = 0
        self.asr_silence_tick_count = 0
        self.asr_force_finalize_requested = False
        self.asr_force_finalize_requested_mono_ms = 0
        self.asr_client_end_best_sentence = ""
        self.asr_client_end_best_score = 0.0
        self.asr_client_end_best_committed_len = 0
        self.asr_committed_end_time_ms = 0
        self.asr_last_partial_sentence = ""

    def _longest_common_prefix(self, texts: List[str]) -> str:
        if not texts:
            return ""
        prefix = texts[0]
        for text in texts[1:]:
            limit = min(len(prefix), len(text))
            idx = 0
            while idx < limit and prefix[idx] == text[idx]:
                idx += 1
            prefix = prefix[:idx]
            if not prefix:
                break
        return prefix

    def _extract_definite_prefix(self, utterances: Any) -> str:
        if not isinstance(utterances, list) or not utterances:
            return ""
        parts: List[str] = []
        for utterance in utterances:
            if not isinstance(utterance, dict):
                break
            if utterance.get("definite") is not True:
                break
            text = self._extract_utterance_text(utterance)
            if text:
                parts.append(text)
        return "".join(parts)

    def _merge_snapshot_tail_for_no_rewind(
        self, committed: str, snapshot: str
    ) -> tuple[str, bool, int]:
        """
        Keep committed prefix immutable while trying to preserve appendable tail
        from a rewritten snapshot.
        """
        if not committed:
            return snapshot, False, 0
        if not snapshot:
            return committed, False, 0
        if snapshot.startswith(committed):
            return snapshot, False, len(committed)

        max_anchor = min(len(committed), 24)
        for anchor_len in range(max_anchor, 3, -1):
            anchor = committed[-anchor_len:]
            pos = snapshot.rfind(anchor)
            if pos < 0:
                continue
            merged = committed + snapshot[pos + anchor_len :]
            if len(merged) >= len(committed):
                return merged, True, anchor_len
        return committed, False, 0

    def _to_int_or_none(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _extract_utterance_char_tokens(
        self,
        utterances: Any,
        fallback_text: str,
    ) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        if isinstance(utterances, list):
            for utterance in utterances:
                if not isinstance(utterance, dict):
                    continue
                definite_raw = utterance.get("definite")
                if definite_raw is True:
                    definite: Optional[bool] = True
                elif definite_raw is False:
                    definite = False
                else:
                    definite = None

                words = utterance.get("words")
                if isinstance(words, list) and len(words) > 0:
                    for word in words:
                        if not isinstance(word, dict):
                            continue
                        word_text = str(word.get("text", "") or "")
                        if not word_text:
                            continue
                        start_time = self._to_int_or_none(word.get("start_time"))
                        end_time = self._to_int_or_none(word.get("end_time"))
                        for ch in word_text:
                            tokens.append(
                                {
                                    "text": ch,
                                    "definite": definite,
                                    "start_time": start_time,
                                    "end_time": end_time,
                                }
                            )
                    continue

                utter_text = self._extract_utterance_text(utterance)
                if not utter_text:
                    continue
                start_time = self._to_int_or_none(utterance.get("start_time"))
                end_time = self._to_int_or_none(utterance.get("end_time"))
                for ch in utter_text:
                    tokens.append(
                        {
                            "text": ch,
                            "definite": definite,
                            "start_time": start_time,
                            "end_time": end_time,
                        }
                    )

        if tokens:
            return tokens
        return [
            {"text": ch, "definite": None, "start_time": None, "end_time": None}
            for ch in (fallback_text or "")
        ]

    def _tokens_text(self, tokens: List[Dict[str, Any]], start: int = 0) -> str:
        if start < 0:
            start = 0
        return "".join(str(token.get("text", "") or "") for token in tokens[start:])

    def _quality_from_candidate(
        self,
        *,
        score: float,
        ratio: float,
        text_len: int,
        committed_len: int,
    ) -> float:
        growth = max(0, text_len - committed_len)
        return float(score) + (float(ratio) * 5.0) + min(20.0, growth / 10.0)

    def _build_anchor_candidate(
        self, committed: str, snapshot: str
    ) -> Optional[Dict[str, Any]]:
        merged, recovered, anchor_len = self._merge_snapshot_tail_for_no_rewind(
            committed, snapshot
        )
        if not recovered:
            return None
        if not merged.startswith(committed):
            return None
        ratio = anchor_len / max(1.0, min(float(len(committed)), float(len(snapshot))))
        score = float(anchor_len * 2)
        return {
            "source": "anchor",
            "text": merged,
            "score": score,
            "ratio": ratio,
            "tail_recovered": True,
        }

    def _build_time_candidate(
        self,
        committed: str,
        snapshot_tokens: List[Dict[str, Any]],
        committed_end_time_ms: int,
    ) -> Optional[Dict[str, Any]]:
        if committed_end_time_ms <= 0 or not snapshot_tokens:
            return None
        best_idx = -1
        best_penalty = 10**9
        for idx, token in enumerate(snapshot_tokens):
            start_time = self._to_int_or_none(token.get("start_time"))
            if start_time is None:
                continue
            penalty = abs(start_time - committed_end_time_ms)
            if start_time < committed_end_time_ms - 220:
                penalty += 500
            if penalty < best_penalty:
                best_penalty = penalty
                best_idx = idx
        if best_idx < 0 or best_idx >= len(snapshot_tokens):
            return None
        tail_text = self._tokens_text(snapshot_tokens, start=best_idx)
        if not tail_text:
            return None
        monotonic_hits = 0
        checked = 0
        for token in snapshot_tokens[best_idx : best_idx + 12]:
            start_time = self._to_int_or_none(token.get("start_time"))
            if start_time is None:
                continue
            checked += 1
            if start_time >= committed_end_time_ms - 120:
                monotonic_hits += 1
        score = 6.0 + (monotonic_hits * 0.9)
        ratio = (monotonic_hits / checked) if checked > 0 else 0.0
        return {
            "source": "time",
            "text": committed + tail_text,
            "score": float(score),
            "ratio": float(ratio),
            "tail_recovered": True,
        }

    def _build_dp_candidate(
        self,
        committed: str,
        snapshot_tokens: List[Dict[str, Any]],
        committed_end_time_ms: int,
    ) -> Optional[Dict[str, Any]]:
        if not committed or not snapshot_tokens:
            return None
        tail_chars = list(committed[-ASR_ALIGN_TAIL_WINDOW_TOKENS:])
        head_tokens = snapshot_tokens[:ASR_ALIGN_HEAD_WINDOW_TOKENS]
        head_chars = [str(token.get("text", "") or "") for token in head_tokens]
        if not tail_chars or not head_chars:
            return None
        m = len(tail_chars)
        n = len(head_chars)
        scores = [[0.0] * (n + 1) for _ in range(m + 1)]
        pointers = [[0] * (n + 1) for _ in range(m + 1)]  # 0 stop,1 diag,2 up,3 left
        best_score = 0.0
        best_i = 0
        best_j = 0
        for i in range(1, m + 1):
            a = tail_chars[i - 1]
            for j in range(1, n + 1):
                token = head_tokens[j - 1]
                b = head_chars[j - 1]
                match_score = -1.0
                if a == b:
                    match_score = 2.0
                    if token.get("definite") is True:
                        match_score += 1.0
                    start_time = self._to_int_or_none(token.get("start_time"))
                    if start_time is not None and committed_end_time_ms > 0:
                        if start_time < committed_end_time_ms - 220:
                            match_score -= 2.0
                        else:
                            match_score += 0.5
                diag = scores[i - 1][j - 1] + match_score
                up = scores[i - 1][j] - 1.0
                left = scores[i][j - 1] - 1.0
                best_cell = 0.0
                ptr = 0
                if diag >= up and diag >= left and diag > 0.0:
                    best_cell = diag
                    ptr = 1
                elif up >= left and up > 0.0:
                    best_cell = up
                    ptr = 2
                elif left > 0.0:
                    best_cell = left
                    ptr = 3
                scores[i][j] = best_cell
                pointers[i][j] = ptr
                if best_cell > best_score:
                    best_score = best_cell
                    best_i = i
                    best_j = j
        if best_score <= 0.0:
            return None
        i = best_i
        j = best_j
        matched_chars = 0
        while i > 0 and j > 0 and pointers[i][j] != 0:
            ptr = pointers[i][j]
            if ptr == 1:
                if tail_chars[i - 1] == head_chars[j - 1]:
                    matched_chars += 1
                i -= 1
                j -= 1
            elif ptr == 2:
                i -= 1
            else:
                j -= 1
        cut_idx = max(0, best_j)
        tail_text = self._tokens_text(snapshot_tokens, start=cut_idx)
        if not tail_text:
            return None
        ratio = matched_chars / max(1.0, min(float(m), float(n)))
        return {
            "source": "dp",
            "text": committed + tail_text,
            "score": float(best_score),
            "ratio": float(ratio),
            "tail_recovered": True,
        }

    def _pick_rewrite_candidate(
        self,
        *,
        committed: str,
        snapshot_text: str,
        snapshot_tokens: List[Dict[str, Any]],
        previous_text: str,
        committed_end_time_ms: int,
    ) -> Optional[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        anchor = self._build_anchor_candidate(committed, snapshot_text)
        if anchor:
            candidates.append(anchor)
        time_candidate = self._build_time_candidate(
            committed, snapshot_tokens, committed_end_time_ms
        )
        if time_candidate:
            candidates.append(time_candidate)
        dp_candidate = self._build_dp_candidate(
            committed, snapshot_tokens, committed_end_time_ms
        )
        if dp_candidate:
            candidates.append(dp_candidate)
        if not candidates:
            return None

        old_quality = self._quality_from_candidate(
            score=0.0,
            ratio=1.0 if previous_text.startswith(committed) else 0.0,
            text_len=len(previous_text),
            committed_len=len(committed),
        )
        accepted: List[Dict[str, Any]] = []
        for candidate in candidates:
            text = str(candidate.get("text", "") or "")
            score = float(candidate.get("score", 0.0))
            ratio = float(candidate.get("ratio", 0.0))
            quality = self._quality_from_candidate(
                score=score,
                ratio=ratio,
                text_len=len(text),
                committed_len=len(committed),
            )
            is_accepted = (
                text.startswith(committed)
                and len(text) >= len(committed)
                and score >= ASR_ALIGN_MIN_SCORE
                and ratio >= ASR_ALIGN_MIN_MATCH_RATIO
                and quality >= old_quality - ASR_REWRITE_QUALITY_TOLERANCE
            )
            self._log(
                "ASR_REWRITE_CANDIDATE "
                f"source={candidate.get('source','-')} "
                f"score={score:.2f} "
                f"ratio={ratio:.3f} "
                f"accepted={1 if is_accepted else 0}"
            )
            if is_accepted:
                candidate["quality"] = quality
                accepted.append(candidate)
        if not accepted:
            return None
        accepted.sort(
            key=lambda item: (
                float(item.get("quality", 0.0)),
                float(item.get("score", 0.0)),
                float(item.get("ratio", 0.0)),
                len(str(item.get("text", "") or "")),
            ),
            reverse=True,
        )
        return accepted[0]

    def _estimate_committed_end_time_ms(
        self, tokens: List[Dict[str, Any]], committed_len: int
    ) -> int:
        if committed_len <= 0 or not tokens:
            return 0
        consumed = 0
        for token in tokens:
            text = str(token.get("text", "") or "")
            if not text:
                continue
            consumed += len(text)
            if consumed >= committed_len:
                end_time = self._to_int_or_none(token.get("end_time"))
                return end_time or 0
        last_end = self._to_int_or_none(tokens[-1].get("end_time"))
        return last_end or 0

    def _log_asr_stream_reset(self, reason: str) -> None:
        now_ms = self._mono_ms()
        since_last_ms = -1
        if self.asr_stream_last_reset_mono_ms > 0:
            since_last_ms = max(0, now_ms - self.asr_stream_last_reset_mono_ms)
        self.asr_stream_last_reset_mono_ms = now_ms
        self.asr_stream_reset_count += 1
        self._log(
            "ASR_STREAM_RESET "
            f"reason={reason or 'unknown'} "
            f"reset_count={self.asr_stream_reset_count} "
            f"since_last_reset_ms={since_last_ms}"
        )

    def _should_drop_stale_asr_packet(self, stream_connect_id: str) -> bool:
        if not self.asr_drop_stale_packets:
            return False

        current_connect_id = (stream_connect_id or "").strip()
        if not current_connect_id:
            return False

        stale_connect_id = (self.asr_stale_connect_id or "").strip()
        if stale_connect_id and current_connect_id == stale_connect_id:
            self._log(
                "ASR_STALE_PACKET_DROPPED "
                f"stale_connect_id={stale_connect_id} "
                f"current_connect_id={current_connect_id}"
            )
            return True

        self._log(
            "ASR_STALE_GUARD_CLEARED "
            f"stale_connect_id={stale_connect_id or '-'} "
            f"current_connect_id={current_connect_id}"
        )
        self.asr_stale_connect_id = ""
        self.asr_drop_stale_packets = False
        return False

    def _evaluate_pending_asr_turn_end(self) -> Optional[tuple[str, int]]:
        if self.state != StateIdle:
            return None
        if not self.asr_buffer or self.asr_last_growth_mono_ms <= 0:
            return None

        silence_ms = max(0, self._mono_ms() - self.asr_last_growth_mono_ms)
        self.asr_no_input_duration = silence_ms
        reason = "client_end_answer" if self.asr_force_finalize_requested else ""
        if not reason:
            if ASR_MANUAL_TURN_END_ENABLED:
                return None
            self.asr_silence_tick_count += 1
            if self.asr_silence_tick_count % ASR_SILENCE_LOG_EVERY_TICKS == 0:
                self._log(
                    f"ASR_SILENCE_TICK silence_ms={silence_ms} text_len={len(self.asr_buffer)}"
                )

            if silence_ms < ASRInterval:
                return None
            reason = "silence_timeout"
        elif ASR_CLIENT_END_STABILIZE_MS > 0:
            elapsed_ms = max(
                0, self._mono_ms() - max(0, self.asr_force_finalize_requested_mono_ms)
            )
            if elapsed_ms < ASR_CLIENT_END_STABILIZE_MS:
                return None
        return reason, silence_ms

    async def _finalize_asr_turn(
        self, *, reason: str, silence_ms: int
    ) -> SentenceRecognizedPayload:
        sentence = self.asr_buffer
        if reason == "client_end_answer" and self.asr_client_end_best_sentence:
            if self.asr_client_end_best_sentence != sentence:
                self._log(
                    "ASR_CLIENT_END_BEST_APPLIED "
                    f"best_len={len(self.asr_client_end_best_sentence)} "
                    f"current_len={len(sentence)} "
                    f"best_score={float(self.asr_client_end_best_score or 0.0):.2f}"
                )
            sentence = self.asr_client_end_best_sentence
        self._log(
            f"ASR_TURN_END reason={reason} silence_ms={silence_ms} text_len={len(sentence)}"
        )
        stale_connect_id = str(getattr(self.asr_client, "connect_id", "") or "")
        self.asr_stale_connect_id = stale_connect_id
        self.asr_drop_stale_packets = True
        self._log(
            "ASR_STALE_GUARD_ENABLED "
            f"stale_connect_id={stale_connect_id or '-'} "
            f"reason={reason}"
        )
        self._reset_asr_buffer_state()
        if self.asr_client:
            await self.asr_client.close()
        return SentenceRecognizedPayload(sentence=sentence)

    async def _finalize_asr_turn_if_silent(self) -> Optional[SentenceRecognizedPayload]:
        pending = self._evaluate_pending_asr_turn_end()
        if pending is None:
            return None
        reason, silence_ms = pending
        return await self._finalize_asr_turn(reason=reason, silence_ms=silence_ms)

    def _extract_utterance_text(self, utterance: Any) -> str:
        if isinstance(utterance, dict):
            text = utterance.get("text", "")
            if text is None:
                text = ""
            if not isinstance(text, str):
                text = str(text)
            return text
        return ""

    def _extract_utterance_segments(
        self, utterances: Any
    ) -> List[tuple[str, Optional[bool]]]:
        if not isinstance(utterances, list):
            return []
        segments: List[tuple[str, Optional[bool]]] = []
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            text = self._extract_utterance_text(utterance)
            if not text:
                continue
            definite_raw = utterance.get("definite")
            if definite_raw is True:
                definite: Optional[bool] = True
            elif definite_raw is False:
                definite = False
            else:
                definite = None
            if segments and segments[-1][1] == definite:
                segments[-1] = (segments[-1][0] + text, definite)
            else:
                segments.append((text, definite))
        return segments

    def _render_snapshot_segments(
        self,
        segments: List[tuple[str, Optional[bool]]],
        *,
        wrap_indefinite: bool,
    ) -> str:
        parts: List[str] = []
        for text, definite in segments:
            if not text:
                continue
            if wrap_indefinite and definite is False:
                parts.append(f"（{text}）")
            else:
                parts.append(text)
        return "".join(parts)

    def _snapshot_segments_from_text(self, text: str) -> List[tuple[str, Optional[bool]]]:
        if not text:
            return []
        return [(text, None)]

    def _slice_snapshot_segments(
        self,
        segments: List[tuple[str, Optional[bool]]],
        *,
        start: int = 0,
        length: Optional[int] = None,
    ) -> List[tuple[str, Optional[bool]]]:
        if not segments:
            return []
        result: List[tuple[str, Optional[bool]]] = []
        consumed = 0
        span_start = max(0, start)
        span_end = None if length is None else span_start + max(0, length)
        for text, definite in segments:
            seg_start = consumed
            seg_end = consumed + len(text)
            consumed = seg_end
            if seg_end <= span_start:
                continue
            if span_end is not None and seg_start >= span_end:
                break
            local_start = max(0, span_start - seg_start)
            local_end = len(text) if span_end is None else min(len(text), span_end - seg_start)
            if local_end <= local_start:
                continue
            chunk = text[local_start:local_end]
            if not chunk:
                continue
            if result and result[-1][1] == definite:
                result[-1] = (result[-1][0] + chunk, definite)
            else:
                result.append((chunk, definite))
        return result

    def _maybe_update_client_end_best_candidate(
        self,
        sentence: str,
        *,
        align_score: float = 0.0,
        align_ratio: float = 0.0,
    ) -> None:
        if not self.asr_force_finalize_requested or not sentence:
            return
        committed_len = len(sentence)
        self.asr_client_end_best_sentence = sentence
        self.asr_client_end_best_score = self._quality_from_candidate(
            score=0.0,
            ratio=1.0,
            text_len=len(sentence),
            committed_len=committed_len,
        )
        self.asr_client_end_best_committed_len = committed_len

    def _apply_asr_response_packet(
        self, response: SaucASRFullServerResponse
    ) -> tuple[str, Optional[SentencePartialRecognizedPayload]]:
        if self.state != StateIdle:
            self._log("service is InProgress, will ignore the newer asr response")
            return "ignored", None
        if self._should_drop_stale_asr_packet(
            getattr(response, "stream_connect_id", "") or ""
        ):
            return "ignored", None
        if not response.result:
            return "ignored", None

        previous_text = self.asr_buffer
        snapshot_text = response.result.text
        if snapshot_text is None:
            snapshot_text = ""
        if not isinstance(snapshot_text, str):
            snapshot_text = str(snapshot_text)

        utterances = response.result.utterances if response.result else []
        if ASR_USE_UTTERANCES and isinstance(utterances, list) and len(utterances) > 0:
            utterance_texts = [
                self._extract_utterance_text(utterance) for utterance in utterances
            ]
            utterance_texts = [text for text in utterance_texts if text]
            if utterance_texts:
                snapshot_text = "".join(utterance_texts)
        snapshot_tokens = self._extract_utterance_char_tokens(utterances, snapshot_text)

        self.asr_snapshot_text = snapshot_text
        next_text = snapshot_text
        self.asr_committed_text = next_text
        self.asr_draft_text = ""
        self.asr_recent_snapshots = [next_text] if next_text else []
        self.asr_committed_end_time_ms = self._estimate_committed_end_time_ms(
            snapshot_tokens,
            len(next_text),
        )

        increment_len = len(next_text) - len(previous_text)
        text_changed = next_text != previous_text
        self.asr_buffer = next_text
        self._maybe_update_client_end_best_candidate(self.asr_buffer)

        if text_changed:
            self.asr_last_growth_mono_ms = self._mono_ms()
            self.asr_no_input_duration = 0
            self.asr_silence_tick_count = 0
            if response.audio:
                self.asr_last_duration = response.audio.duration
        elif self.asr_last_growth_mono_ms > 0:
            self.asr_no_input_duration = max(
                0, self._mono_ms() - self.asr_last_growth_mono_ms
            )

        if increment_len < 0:
            self._log(
                "ASR_TEXT_REWRITE "
                f"old_len={len(previous_text)} "
                f"new_len={len(next_text)} "
                f"delta={increment_len}"
            )
        self._log(
            f"asr buffer incremented: {increment_len}, utterances: {response.result.utterances}"
        )
        partial_payload = self._build_asr_partial_payload()
        return ("changed" if text_changed else "unchanged"), partial_payload

    def _build_asr_partial_payload(self) -> Optional[SentencePartialRecognizedPayload]:
        if not self.emit_asr_partial_events:
            return None
        if not self.asr_buffer:
            return None
        if self.asr_buffer == self.asr_last_partial_sentence:
            return None
        self.asr_last_partial_sentence = self.asr_buffer
        return SentencePartialRecognizedPayload(sentence=self.asr_buffer)

    def _start_turn(self) -> None:
        self.current_turn_id = str(uuid.uuid4())
        self.turn_timestamps_ms = {}

    def _end_turn(self) -> None:
        self.current_turn_id = None
        self.turn_timestamps_ms = None

    def _duration_ms(self, start_key: str, end_key: str) -> Optional[int]:
        if not self.turn_timestamps_ms:
            return None
        start_ts = self.turn_timestamps_ms.get(start_key)
        end_ts = self.turn_timestamps_ms.get(end_key)
        if start_ts is None or end_ts is None:
            return None
        return max(0, end_ts - start_ts)

    def _log_turn_event(
        self,
        event: str,
        extra: Optional[Dict[str, Any]] = None,
        *,
        state: Optional[str] = None,
        store_ts: bool = True,
    ) -> None:
        if not self.current_turn_id:
            return
        ts_mono_ms = self._mono_ms()
        if self.turn_timestamps_ms is None:
            self.turn_timestamps_ms = {}
        if store_ts and event not in self.turn_timestamps_ms:
            self.turn_timestamps_ms[event] = ts_mono_ms
        payload = {
            "session_id": self.session_id or "unknown",
            "turn_id": self.current_turn_id,
            "ts_wall": self._wall_time_iso(),
            "ts_mono_ms": ts_mono_ms,
            "event": event,
            "state": state or self.state,
            "extra": extra or {},
        }
        self._log(f"[TurnTrace] {json.dumps(payload, ensure_ascii=False)}")

    def _log_turn_latency_breakdown(self, *, status: str) -> None:
        if not self.current_turn_id:
            return
        metrics = {
            "rec_to_judge_end_ms": self._duration_ms("turn_recognized_emitted", "judge_end"),
            "judge_ms": self._duration_ms("judge_start", "judge_end"),
            "judge_end_to_llm2_first_token_ms": self._duration_ms(
                "judge_end", "interviewer_llm_first_token"
            ),
            "llm2_ttft_ms": self._duration_ms(
                "interviewer_llm_start", "interviewer_llm_first_token"
            ),
            "llm2_total_ms": self._duration_ms("interviewer_llm_start", "interviewer_llm_end"),
            "tts_init_ms": self._duration_ms("tts_init_start", "tts_init_end"),
            "tts_stream_to_first_sentence_ms": self._duration_ms(
                "tts_stream_start", "tts_first_sentence_start"
            ),
            "rec_to_first_sentence_ms": self._duration_ms(
                "turn_recognized_emitted", "tts_first_sentence_start"
            ),
            "rec_to_tts_done_ms": self._duration_ms("turn_recognized_emitted", "tts_done"),
        }
        payload = {
            "session_id": self.session_id or "unknown",
            "turn_id": self.current_turn_id,
            "ts_wall": self._wall_time_iso(),
            "ts_mono_ms": self._mono_ms(),
            "event": "turn_latency_breakdown",
            "state": self.state,
            "status": status,
            "metrics": metrics,
        }
        self._log(f"[TurnTrace] {json.dumps(payload, ensure_ascii=False)}")

    def _emit_bot_text(self, text: str) -> None:
        if not text or not self.on_bot_sentence:
            return
        try:
            self.on_bot_sentence(text)
        except Exception as callback_error:
            self._log(f"[InterviewPersist] on_bot_sentence callback failed: {callback_error}")

    def _emit_candidate_text(self, text: str) -> None:
        if not text or not self.on_candidate_sentence:
            return
        try:
            self.on_candidate_sentence(text)
        except Exception as callback_error:
            self._log(
                f"[InterviewPersist] on_candidate_sentence callback failed: {callback_error}"
            )

    def _emit_bot_audio_chunk(self, chunk: bytes) -> None:
        if not chunk or not self.on_bot_audio_chunk:
            return
        try:
            self.on_bot_audio_chunk(chunk)
        except Exception as callback_error:
            self._log(f"[InterviewPersist] on_bot_audio_chunk callback failed: {callback_error}")

    def _emit_interview_completed(self) -> None:
        if not self.on_interview_completed:
            return
        try:
            self.on_interview_completed()
        except Exception as callback_error:
            self._log(
                f"[InterviewPersist] on_interview_completed callback failed: {callback_error}"
            )

    def _emit_interview_runtime_checkpoint(self) -> None:
        if not self.on_interview_runtime_checkpoint or not self.interview_flow:
            return
        try:
            checkpoint = self.interview_flow.export_runtime_checkpoint()
            self.on_interview_runtime_checkpoint(checkpoint)
        except Exception as callback_error:
            self._log(
                "[InterviewPersist] on_interview_runtime_checkpoint callback failed: "
                f"{callback_error}"
            )

    def build_interview_score_inputs(self) -> List[Dict[str, Any]]:
        if not self.interview_mode or not self.interview_flow:
            return []
        snapshots: List[QuestionAnswerSnapshot] = (
            self.interview_flow.export_question_answer_snapshots()
        )
        if not snapshots:
            return []

        segments: List[Dict[str, Any]] = []
        previous_scene = ""
        for snapshot in snapshots:
            evidence = snapshot.evidence or {}
            scene = str(evidence.get("scenario", "") or "").strip()
            should_start_new_segment = False
            if scene:
                should_start_new_segment = (not segments) or (scene != previous_scene)
            else:
                should_start_new_segment = True

            if should_start_new_segment:
                segments.append(
                    {
                        "scene": scene,
                        "questions": [],
                        "candidate_answers": [],
                        "scoring_boundary": "",
                        "score_format": "",
                        "ability_dimension": "",
                        "comment_requirement": "",
                    }
                )
            current_segment = segments[-1]
            current_segment["questions"].append(str(snapshot.question or "").strip())
            current_segment["candidate_answers"].extend(snapshot.candidate_answers or [])
            if not current_segment["scoring_boundary"]:
                current_segment["scoring_boundary"] = str(
                    evidence.get("scoring_boundary", "") or ""
                ).strip()
            if not current_segment["score_format"]:
                current_segment["score_format"] = str(
                    evidence.get("score_format", "") or ""
                ).strip()
            if not current_segment["ability_dimension"]:
                current_segment["ability_dimension"] = str(
                    evidence.get("ability_dimension", "") or ""
                ).strip()
            if not current_segment["comment_requirement"]:
                current_segment["comment_requirement"] = str(
                    evidence.get("comment_requirement", "") or ""
                ).strip()
            previous_scene = scene

        items: List[Dict[str, Any]] = []
        for idx, segment in enumerate(segments, start=1):
            scene = str(segment.get("scene", "") or "").strip()
            questions = [q for q in segment.get("questions", []) if str(q or "").strip()]
            candidate_answers = [
                str(answer or "").strip()
                for answer in segment.get("candidate_answers", [])
                if str(answer or "").strip()
            ]
            if scene:
                question_text = f"[场景] {scene}\n" + "\n".join(
                    f"{i + 1}. {q}" for i, q in enumerate(questions)
                )
            else:
                question_text = "\n".join(questions)
            items.append(
                {
                    "question_id": f"scene-{idx}",
                    "sort_order": idx,
                    "question": question_text.strip(),
                    "ability_dimension": str(segment.get("ability_dimension", "") or "").strip(),
                    "scoring_boundary": str(segment.get("scoring_boundary", "") or "").strip(),
                    "best_standard": "",
                    "medium_standard": "",
                    "worst_standard": "",
                    "score_format": str(segment.get("score_format", "") or "").strip(),
                    "comment_requirement": str(
                        segment.get("comment_requirement", "") or ""
                    ).strip(),
                    "candidate_answers": candidate_answers,
                    "aggregated_answer": "\n".join(candidate_answers).strip(),
                }
            )
        return items

    async def _greeting_text_stream(self, text: str) -> AsyncIterable[str]:
        """Yield a single greeting text for TTS processing (no LLM call)."""
        yield text

    def _build_asr_unavailable_event(self) -> WebEvent:
        return WebEvent.from_payload(
            BotErrorPayload(
                error=ErrorEvent(
                    code="ASR_INIT_UNAVAILABLE",
                    message="语音识别服务暂时不可用，请检查网络后重试",
                )
            )
        )

    async def _ensure_asr_ready(self) -> bool:
        if not self.asr_client:
            self._log("ASR_INIT_FAIL reason=client_missing")
            return False
        if self.asr_client.inited:
            return True

        for attempt in range(1, ASR_INIT_MAX_ATTEMPTS + 1):
            self._log(
                f"ASR_INIT_START attempt={attempt} timeout={ASR_INIT_TIMEOUT_SECONDS}s"
            )
            try:
                await asyncio.wait_for(
                    self.asr_client.init(), timeout=ASR_INIT_TIMEOUT_SECONDS
                )
                self._log(f"ASR_INIT_OK attempt={attempt}")
                return True
            except asyncio.TimeoutError:
                self._log(
                    f"ASR_INIT_TIMEOUT attempt={attempt} timeout={ASR_INIT_TIMEOUT_SECONDS}s"
                )
            except Exception as init_err:
                self._log(f"ASR_INIT_FAIL attempt={attempt} stage=init error={init_err}")

            try:
                await self.asr_client.close()
            except Exception as close_err:
                self._log(f"ASR_INIT_FAIL attempt={attempt} stage=close error={close_err}")

            if attempt < ASR_INIT_MAX_ATTEMPTS:
                self._log(
                    f"ASR_INIT_RETRY next_attempt={attempt + 1} "
                    f"backoff={ASR_INIT_RETRY_BACKOFF_SECONDS}s"
                )
                await asyncio.sleep(ASR_INIT_RETRY_BACKOFF_SECONDS)

        self._log(f"ASR_INIT_FAIL attempts={ASR_INIT_MAX_ATTEMPTS}")
        return False

    async def send_greeting(self) -> AsyncIterable[WebEvent]:
        """Send an initial greeting through TTS before the conversation starts."""
        greeting_stream = self._greeting_text_stream(GREETING_TEXT)
        async for payload in self.handle_tts_response(greeting_stream):
            yield WebEvent.from_payload(payload)
        self._append_history_message("assistant", GREETING_TEXT)
        self._emit_bot_text(GREETING_TEXT)

    async def handler_loop(
        self, inputs: AsyncIterable[WebEvent]
    ) -> AsyncIterable[WebEvent]:
        """
        Main loop for handling input events and generating responses.
        """
        if self.interview_mode:
            async for event in self._interview_handler_loop(inputs):
                yield event
            return

        # Original chat-persona loop
        # Send greeting before starting the conversation loop
        async for event in self.send_greeting():
            yield event

        try:
            while True:
                self.asr_last_stream_end_reason = ""
                asr_responses = await self.handle_input_event(inputs)
                async for asr_payload in self.handle_asr_response(asr_responses):
                    if isinstance(asr_payload, SentencePartialRecognizedPayload):
                        yield WebEvent.from_payload(asr_payload)
                        continue

                    asr_recognized = asr_payload
                    # set state into InProgress
                    self.state = StateInProgress
                    yield WebEvent.from_payload(asr_recognized)
                    llm_stream_rsp = self.stream_llm_chat(asr_recognized.sentence)
                    async for payload in self.handle_tts_response(llm_stream_rsp):
                        yield WebEvent.from_payload(payload)
                    # recreate the asr and tts client
                    self.state = StateIdle
                self._log_asr_stream_reset(
                    self.asr_last_stream_end_reason or "upstream_closed"
                )
                await asyncio.sleep(0.05)
        except ASRInitUnavailableError as asr_err:
            self.state = StateIdle
            self._log(f"ASR_INIT_FATAL error={asr_err}")
            yield self._build_asr_unavailable_event()
            return

    async def handle_input_event(
        self, inputs: AsyncIterable[WebEvent]
    ) -> AsyncIterable[SaucASRFullServerResponse]:
        """
        Handle input events and generate ASR responses.
        """
        fatal_error_queue: asyncio.Queue[ASRInitUnavailableError] = asyncio.Queue(maxsize=1)

        async def async_gen() -> AsyncIterable[bytes]:
            async for input_event in inputs:
                if self.state != StateIdle:
                    self._log("service is InProgress, will ignore the incoming input")
                    continue
                elif not self.asr_client.inited:
                    self._log("need recreate asr conn")
                    ok = await self._ensure_asr_ready()
                    if not ok:
                        self.asr_init_failure_streak += 1
                        self._log(
                            f"ASR_INIT_STREAK value={self.asr_init_failure_streak} "
                            f"threshold={ASR_INIT_FATAL_FAILURE_STREAK}"
                        )
                        if self.asr_init_failure_streak >= ASR_INIT_FATAL_FAILURE_STREAK:
                            self._log(
                                "ASR_INIT_FATAL reason=too_many_consecutive_failures "
                                f"streak={self.asr_init_failure_streak}"
                            )
                            with contextlib.suppress(Exception):
                                await self.asr_client.close()
                            with contextlib.suppress(asyncio.QueueFull):
                                fatal_error_queue.put_nowait(
                                    ASRInitUnavailableError(
                                        "ASR init failed repeatedly"
                                    )
                                )
                            return
                        continue
                    self.asr_init_failure_streak = 0

                self._log(
                    f"receive input, event={input_event.event} payload={input_event.payload}"
                )
                if input_event.event == BOT_UPDATE_CONFIG and isinstance(
                    input_event.payload, BotUpdateConfigPayload
                ):
                    self.tts_speaker = input_event.payload.speaker
                elif input_event.event == CLIENT_END_ANSWER:
                    self.asr_force_finalize_requested = True
                    self.asr_force_finalize_requested_mono_ms = self._mono_ms()
                    self.asr_client_end_best_sentence = self.asr_buffer
                    self.asr_client_end_best_score = self._quality_from_candidate(
                        score=0.0,
                        ratio=1.0 if self.asr_buffer.startswith(self.asr_committed_text) else 0.0,
                        text_len=len(self.asr_buffer),
                        committed_len=len(self.asr_committed_text),
                    )
                    self.asr_client_end_best_committed_len = len(self.asr_committed_text)
                    self._log("ASR_TURN_END_REQUEST source=client_end_answer")
                elif input_event.event == CLIENT_HANGUP:
                    self.asr_force_finalize_requested = True
                    self.asr_force_finalize_requested_mono_ms = self._mono_ms()
                    self.asr_client_end_best_sentence = self.asr_buffer
                    self.asr_client_end_best_score = self._quality_from_candidate(
                        score=0.0,
                        ratio=1.0 if self.asr_buffer.startswith(self.asr_committed_text) else 0.0,
                        text_len=len(self.asr_buffer),
                        committed_len=len(self.asr_committed_text),
                    )
                    self.asr_client_end_best_committed_len = len(self.asr_committed_text)
                    self._log("ASR_TURN_END_REQUEST source=client_hangup")
                elif input_event.event == USER_AUDIO and input_event.data:
                    yield input_event.data

        asr_stream = self.asr_client.stream_asr(async_gen())

        async def merged_stream() -> AsyncIterable[SaucASRFullServerResponse]:
            asr_iter = asr_stream.__aiter__()
            while True:
                next_asr_task = asyncio.create_task(asr_iter.__anext__())
                fatal_error_task = asyncio.create_task(fatal_error_queue.get())
                done, pending = await asyncio.wait(
                    {next_asr_task, fatal_error_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if fatal_error_task in done:
                    next_asr_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await next_asr_task
                    raise fatal_error_task.result()

                try:
                    rsp = next_asr_task.result()
                except StopAsyncIteration:
                    self.asr_last_stream_end_reason = (
                        "upstream_closed"
                        if (self.asr_client is not None and not self.asr_client.inited)
                        else "source_exhausted"
                    )
                    break
                yield rsp

        return merged_stream()

    async def handle_asr_response(
        self, asr_responses: AsyncIterable[SaucASRFullServerResponse]
    ) -> AsyncIterable[
        Union[SentenceRecognizedPayload, SentencePartialRecognizedPayload]
    ]:
        """
        Handle ASR responses and generate recognized sentences.
        """
        asr_iter = asr_responses.__aiter__()
        next_asr_task: Optional[asyncio.Task] = None
        while True:
            pending_turn_end = self._evaluate_pending_asr_turn_end()
            if pending_turn_end is not None:
                reason, silence_ms = pending_turn_end
                if reason == "silence_timeout" and ASR_PRE_FINALIZE_GRACE_MS > 0:
                    self._log(
                        "ASR_PRE_FINALIZE_DRAIN_START "
                        f"silence_ms={silence_ms} "
                        f"grace_ms={ASR_PRE_FINALIZE_GRACE_MS} "
                        f"text_len={len(self.asr_buffer)}"
                    )
                    grace_deadline_ms = self._mono_ms() + ASR_PRE_FINALIZE_GRACE_MS
                    aborted_by_late_packet = False
                    while self._mono_ms() < grace_deadline_ms:
                        if next_asr_task is None:
                            next_asr_task = asyncio.create_task(asr_iter.__anext__())
                        remaining_ms = max(0, grace_deadline_ms - self._mono_ms())
                        timeout_seconds = min(
                            ASR_POLL_INTERVAL_SECONDS, remaining_ms / 1000.0
                        )
                        if timeout_seconds <= 0:
                            break
                        try:
                            response = await asyncio.wait_for(
                                asyncio.shield(next_asr_task),
                                timeout=timeout_seconds,
                            )
                        except asyncio.TimeoutError:
                            continue
                        except StopAsyncIteration:
                            next_asr_task = None
                            break
                        else:
                            next_asr_task = None
                        change_status, partial_payload = self._apply_asr_response_packet(response)
                        if partial_payload is not None:
                            yield partial_payload
                        if change_status == "changed":
                            self._log(
                                "ASR_PRE_FINALIZE_DRAIN_ABORTED_BY_LATE_PACKET "
                                f"grace_ms={ASR_PRE_FINALIZE_GRACE_MS} "
                                f"text_len={len(self.asr_buffer)}"
                            )
                            aborted_by_late_packet = True
                            break
                    if aborted_by_late_packet:
                        continue
                    self._log(
                        "ASR_PRE_FINALIZE_DRAIN_TIMEOUT "
                        f"grace_ms={ASR_PRE_FINALIZE_GRACE_MS} "
                        f"text_len={len(self.asr_buffer)}"
                    )
                    pending_turn_end = self._evaluate_pending_asr_turn_end()
                    if pending_turn_end is None:
                        continue
                    reason, silence_ms = pending_turn_end

                yield await self._finalize_asr_turn(reason=reason, silence_ms=silence_ms)
                continue

            if next_asr_task is None:
                next_asr_task = asyncio.create_task(asr_iter.__anext__())
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(next_asr_task),
                    timeout=ASR_POLL_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                next_asr_task = None
                if (
                    self.asr_buffer
                    and self.asr_last_growth_mono_ms > 0
                    and not self.asr_force_finalize_requested
                ):
                    self.asr_force_finalize_requested = True
                    self._log("ASR_TURN_END_REQUEST source=stream_end")
                pending_turn_end = self._evaluate_pending_asr_turn_end()
                if pending_turn_end is not None:
                    reason, silence_ms = pending_turn_end
                    yield await self._finalize_asr_turn(reason=reason, silence_ms=silence_ms)
                elif self.asr_force_finalize_requested:
                    self._log(
                        "ASR_TURN_END_REQUEST_DROPPED reason=no_recognized_text"
                    )
                    self.asr_force_finalize_requested = False
                break
            else:
                next_asr_task = None

            _, partial_payload = self._apply_asr_response_packet(response)
            if partial_payload is not None:
                yield partial_payload

        if next_asr_task is not None:
            next_asr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await next_asr_task

    async def handle_tts_response(
        self, llm_output: AsyncIterable[str]
    ) -> AsyncIterable[
        Union[TTSSentenceStartPayload, TTSSentenceEndPayload, TTSDonePayload]
    ]:
        """
        Handle TTS responses and generate TTS events.
        """
        buffer = bytearray()
        if not self.tts_client.inited:
            self._log("need recreate tts client")
            self._log_turn_event("tts_init_start")
            try:
                await self.tts_client.init()
                self._log_turn_event("tts_init_end")
            except Exception as e:
                self._log_turn_event("tts_init_error", extra={"error": str(e)})
                raise
        self._log_turn_event("tts_stream_start")
        first_sentence_logged = False
        try:
            async for tts_rsp in self.tts_client.tts(
                source=llm_output, include_transcript=True
            ):
                self._log(
                    f"receive tts response: event={tts_rsp.event} transcript={tts_rsp.transcript} \
                    audio len={len(tts_rsp.audio) if tts_rsp.audio else 0}"
                )
                if tts_rsp.event == EventTTSSentenceStart:
                    if not first_sentence_logged:
                        self._log_turn_event(
                            "tts_first_sentence_start",
                            extra={"sentence_len": len(tts_rsp.transcript or "")},
                        )
                        first_sentence_logged = True
                    yield TTSSentenceStartPayload(sentence=tts_rsp.transcript)
                elif tts_rsp.event == EventTTSSentenceEnd:
                    yield TTSSentenceEndPayload(data=buffer)
                    buffer.clear()
                elif tts_rsp.audio:
                    buffer.extend(tts_rsp.audio)
                    self._emit_bot_audio_chunk(tts_rsp.audio)

                if tts_rsp.event == EventSessionFinished:
                    self._log_turn_event("tts_done")
                    yield TTSDonePayload()
                    await self.tts_client.close()
                    break
        except Exception as e:
            self._log_turn_event("tts_stream_error", extra={"error": str(e)})
            raise

    async def stream_llm_chat(self, text: str) -> AsyncIterable[str]:
        """
        Stream chat with the LLM and generate responses.
        """
        self._append_history_message("user", text)

        llm = BaseChatLanguageModel(
            template=VoiceBotPrompt(),
            messages=self.history_messages,
            endpoint_id=self.llm2_endpoint_id,
        )
        completion_buffer = ""

        async with llm_slot():
            async for chunk in llm.astream():
                if chunk.choices and chunk.choices[0].delta:
                    yield chunk.choices[0].delta.content
                    completion_buffer += chunk.choices[0].delta.content

        if completion_buffer:
            self._append_history_message("assistant", completion_buffer)
            self._emit_bot_text(completion_buffer)

    async def _send_scripted_text(self, text: str) -> AsyncIterable[WebEvent]:
        """Send a pre-determined text string through TTS without an LLM call."""
        if self.scripted_tts_cache is None:
            self.scripted_tts_cache = SCRIPTED_TTS_MEMORY_CACHE

        cache_token = (self.interview_token or "").strip()
        cache_speaker = (self.tts_speaker or "").strip()
        cache_text = (text or "").strip()
        use_scripted_cache = bool(cache_token and cache_text and self.scripted_tts_cache)
        scripted_cache_hit: Optional[List[CachedTTSSentence]] = None

        if use_scripted_cache and self.scripted_tts_cache:
            try:
                scripted_cache_hit = self.scripted_tts_cache.get(
                    cache_token,
                    cache_speaker,
                    cache_text,
                )
            except Exception as cache_err:
                self._log(
                    f"[ScriptedTTSCache] read error token={cache_token} "
                    f"text_len={len(cache_text)} error={cache_err}"
                )
                self._log_turn_event(
                    "tts_scripted_cache_read_error",
                    extra={"error": str(cache_err)},
                )
                scripted_cache_hit = None

        if scripted_cache_hit:
            self._log_turn_event(
                "tts_scripted_cache_hit",
                extra={"sentences": len(scripted_cache_hit), "text_len": len(cache_text)},
            )
            for sentence in scripted_cache_hit:
                yield WebEvent.from_payload(
                    TTSSentenceStartPayload(sentence=sentence.transcript)
                )
                self._emit_bot_audio_chunk(sentence.audio)
                yield WebEvent.from_payload(TTSSentenceEndPayload(data=sentence.audio))
            self._log_turn_event("tts_done")
            yield WebEvent.from_payload(TTSDonePayload())
            self._append_history_message("assistant", text)
            self._emit_bot_text(text)
            return

        if use_scripted_cache:
            self._log_turn_event(
                "tts_scripted_cache_miss",
                extra={"text_len": len(cache_text)},
            )

        cached_sentences: List[CachedTTSSentence] = []
        pending_transcripts: List[str] = []
        text_stream = self._greeting_text_stream(text)
        async for payload in self.handle_tts_response(text_stream):
            if isinstance(payload, TTSSentenceStartPayload):
                pending_transcripts.append(payload.sentence or "")
            elif isinstance(payload, TTSSentenceEndPayload):
                transcript = pending_transcripts.pop(0) if pending_transcripts else ""
                audio = bytes(payload.data or b"")
                if audio:
                    cached_sentences.append(
                        CachedTTSSentence(transcript=transcript, audio=audio)
                    )
            yield WebEvent.from_payload(payload)

        if use_scripted_cache and cached_sentences and self.scripted_tts_cache:
            try:
                if self.scripted_tts_cache.set(
                    cache_token,
                    cache_speaker,
                    cache_text,
                    cached_sentences,
                ):
                    self._log_turn_event(
                        "tts_scripted_cache_store_ok",
                        extra={"sentences": len(cached_sentences)},
                    )
            except Exception as cache_err:
                self._log(
                    f"[ScriptedTTSCache] write error token={cache_token} "
                    f"text_len={len(cache_text)} error={cache_err}"
                )
                self._log_turn_event(
                    "tts_scripted_cache_store_error",
                    extra={"error": str(cache_err)},
                )
        self._append_history_message("assistant", text)
        self._emit_bot_text(text)

    def _build_interview_context(
        self,
        decision: Optional[Decision],
        next_question_or_followup: str,
        flow_state: str,
        has_candidate_speech: bool = True,
        is_scene_transition: bool = False,
    ) -> str:
        """Build the interview context string for the main interviewer LLM."""
        parts = []
        transition_intensity = "none" if not has_candidate_speech else "soft"
        if (
            decision
            and decision.next_action == "next_question"
            and has_candidate_speech
            and is_scene_transition
        ):
            transition_intensity = "strong"
        parts.append(f"[过渡强度] {transition_intensity}")
        parts.append(
            "[开场策略] 开场可选且不强制：允许“无开场 / 仅语气词 / 仅过渡语 / 语气词+过渡语”。"
            "优先自然表达；若信息密度高或开场易重复，直接进入题干。"
            "语气词默认可用“嗯/好的/了解”。"
        )

        if not has_candidate_speech:
            parts.append(
                "[首轮首问约束] 候选人尚未发言，直接进入问题本体；禁止使用“进入下一题”“下一个场景”“我们继续”等过渡口吻。"
            )

        if decision:
            parts.append(f"[评估结果] 评判理由: {decision.reason}")
            parts.append(f"[评估结果] 覆盖度得分: {decision.coverage_score:.2f}")

            if decision.next_action == "next_question":
                forced_move_reasons = {
                    "follow_up_limit_reached",
                    "clarify_limit_reached",
                    "global_turn_limit_reached",
                }
                if decision.reason in forced_move_reasons:
                    if is_scene_transition:
                        parts.append(
                            "[指令] 现在需要切换到新场景，请用中性过渡进入下一个场景，不评价回答质量，不夸赞。"
                        )
                    else:
                        parts.append(
                            "[指令] 继续当前场景的后续问题，请中性衔接，不评价回答质量，不夸赞，不要说“进入下一题”。"
                        )
                else:
                    if is_scene_transition:
                        parts.append(
                            "[指令] 候选人回答已覆盖关键点，可用一句简短肯定后切换到下一个场景。"
                        )
                    else:
                        parts.append(
                            "[指令] 候选人回答已覆盖关键点，可用一句简短肯定后直接承接当前场景的后续问题，不要说“进入下一题”。"
                        )
                if has_candidate_speech and decision.reason not in forced_move_reasons:
                    if is_scene_transition:
                        parts.append(
                            "[表达风格] 当前为strong过渡：可提示场景切换，但开场不强制；可选无开场、仅语气词、仅过渡语或语气词+过渡语。"
                            "若使用开场，最多1句（建议不超过20字），随后完整表达[下一步内容]，不得新增信息。"
                        )
                    else:
                        parts.append(
                            "[表达风格] 当前为soft过渡：轻承接即可，开场不强制；可选无开场、仅语气词、仅过渡语或语气词+过渡语。"
                            "若使用开场，最多1句（建议不超过14字），语气中性或轻肯定；随后完整表达[下一步内容]，不得新增信息。"
                        )
            elif decision.next_action == "follow_up":
                parts.append("[指令] 候选人回答不够完整，请礼貌地进行追问以获取更多细节。")
                if decision.next_prompt:
                    parts.append(f"[追问方向] {decision.next_prompt}")
                if has_candidate_speech:
                    parts.append(
                        "[表达风格] 当前为soft过渡：先承接候选人上一轮再聚焦追问，开场不强制；可选无开场、仅语气词、仅过渡语或语气词+过渡语。"
                        "若使用开场，最多1句且不得新增信息，随后完整表达[下一步内容]。"
                    )
            elif decision.next_action == "clarify":
                parts.append(
                    "[指令] 候选人对题意有疑惑，请仅做题意澄清，不要新增考察维度，也不要转成追问。澄清属于解释态，不属于提问态。"
                )
                if decision.next_prompt:
                    parts.append(f"[澄清说明] {decision.next_prompt}")
                if has_candidate_speech:
                    parts.append(
                        "[表达风格] 当前为soft过渡：按“复述题干关键点 -> 一句解释 -> 请按这个题意继续作答（陈述句）”顺序表达；开场不强制。"
                        "如需开场，可选无开场、仅语气词、仅过渡语或语气词+过渡语；解释仅用于澄清题意，不得新增考察点。"
                        "澄清阶段禁止反问、禁止新增问题、禁止问号。"
                    )

        if next_question_or_followup:
            parts.append(f"[下一步内容] {next_question_or_followup}")

        if flow_state == ASK_QUESTION:
            parts.append(
                "[题干保真要求] 你可以口语化表达[下一步内容]，但必须保留全部关键信息："
                "主体对象、场景背景、前提条件、动作要求、活动名/业务名词、全部数字与单位、阈值、比较关系、因果关系。"
                "若[下一步内容]含“背景说明+提问要求”，两部分必须都保留，禁止只输出问题尾句。"
                "若含对立观点前置背景（如“有人说…也有人说…”），该背景必须完整保留。"
                "若[下一步内容]为陈述/任务指令句，不得强行改成确认问句，不得追加“对吗/可以吗/好吗/是吗”等确认尾巴。"
                "禁止省略、合并、替换任何关键数字或条件。"
            )

        if flow_state == ASK_CLARIFY:
            parts.append(
                "[题意澄清要求] 若候选人是在请求重复题目或没听清，先复述题干关键信息，再做一句简短解释；"
                "解释不得新增考察点。"
                "若题干是陈述/任务指令句，保持原题型，不要转成确认问句，也不要新增确认尾巴。"
                "澄清结束句统一使用“请按这个题意继续作答。”。"
                "澄清阶段禁止反问和二选一提问，不要输出问号。"
            )

        if flow_state == WRAP_UP:
            parts.append("[指令] 面试即将结束，请做结束语。")

        return "\n".join(parts)

    def _is_scene_transition_between_questions(
        self, current_question_id: Optional[str], next_question_id: Optional[str]
    ) -> bool:
        if not current_question_id or not next_question_id:
            return False
        current_segment = self.question_context_segments.get(str(current_question_id), "").strip()
        next_segment = self.question_context_segments.get(str(next_question_id), "").strip()
        if not current_segment or not next_segment:
            return False
        return current_segment != next_segment

    def _flow_state_origin(self, flow_state: str) -> str:
        if flow_state == ASK_QUESTION:
            return "bank_question"
        if flow_state == ASK_FOLLOWUP:
            return "follow_up"
        if flow_state == ASK_CLARIFY:
            return "clarify"
        if flow_state == WRAP_UP:
            return "wrap_up"
        return "unknown"

    def _resolve_delivery_state(
        self, flow_state: str, next_response: Optional[FlowResponse]
    ) -> str:
        """Resolve the state that produced current interviewer text."""
        if next_response and next_response.state_before:
            return next_response.state_before
        return flow_state

    def _compute_scene_transition_for_delivery(
        self,
        decision: Optional[Decision],
        delivery_state: str,
        current_question_id: Optional[str],
        next_question_id: Optional[str],
    ) -> bool:
        if not decision or decision.next_action != "next_question":
            return False
        if delivery_state != ASK_QUESTION:
            return False
        return self._is_scene_transition_between_questions(
            current_question_id=current_question_id,
            next_question_id=next_question_id,
        )

    def _clarify_subtype_from_decision(self, decision: Optional[Decision]) -> str:
        if not decision or decision.next_action != "clarify":
            return "-"
        if decision.reason == "candidate_requested_repeat":
            return "repeat_request"
        if decision.reason == "candidate_requested_clarification":
            return "meaning_clarify"
        return "other"

    def _calc_segment_context_char_len(self, interview_context: str) -> int:
        history_messages = self._history_for_current_segment()
        total = len(interview_context or "")
        for message in history_messages:
            total += len(str(getattr(message, "content", "") or ""))
        return total

    async def stream_interview_llm_chat(
        self, interview_context: str
    ) -> AsyncIterable[str]:
        """Stream the main interviewer LLM response (LLM call #2).

        Uses Ark Responses API. The interview_context is passed as an
        ephemeral user message but NOT persisted in history_messages.
        The LLM's output IS added to history.
        """
        messages_for_llm = self._history_for_current_segment() + [
            ArkMessage(**{"role": "user", "content": interview_context})
        ]
        if not self.responses_adapter:
            self.responses_adapter = ArkResponsesAdapter(api_key=self.ark_api_key)
        input_messages = build_input_messages(messages_for_llm)
        completion_buffer = ""
        first_token_logged = False

        async with llm_slot():
            async for delta_text in self.responses_adapter.stream_text(
                model=self.llm2_endpoint_id,
                instructions=INTERVIEWER_SYSTEM_PROMPT,
                messages=input_messages,
                thinking_type=self.llm2_thinking_type,
                reasoning_effort=self.llm2_reasoning_effort,
            ):
                if not delta_text:
                    continue
                if not first_token_logged:
                    self._log_turn_event(
                        "interviewer_llm_first_token",
                        extra={"token_len": len(delta_text)},
                    )
                    first_token_logged = True
                yield delta_text
                completion_buffer += delta_text

        if completion_buffer:
            self._append_history_message("assistant", completion_buffer)
            self._emit_bot_text(completion_buffer)
        self._log_turn_event(
            "interviewer_llm_end",
            extra={"output_len": len(completion_buffer)},
        )

    async def _interview_handler_loop(
        self, inputs: AsyncIterable[WebEvent]
    ) -> AsyncIterable[WebEvent]:
        """Interview-mode main loop using InterviewFlow state machine."""
        flow = self.interview_flow
        if not flow:
            self._log("[Interview] interview_flow unavailable, abort loop")
            return
        self._log("[Interview] Starting interview handler loop")

        async def _produce_interviewer_message_and_emit_checkpoint() -> FlowResponse:
            response = await flow.produce_interviewer_message()
            self._emit_interview_runtime_checkpoint()
            return response

        resume_mode = str(self.interview_resume_mode or RESUME_MODE_NONE).strip()
        self.interview_resume_mode = RESUME_MODE_NONE

        if resume_mode == RESUME_MODE_DONE:
            self._log("[Interview] ResumeMode=done, mark interview completed")
            self._emit_interview_completed()
            return
        if resume_mode == RESUME_MODE_WRAP_UP:
            if flow.state != WRAP_UP:
                flow.state = WRAP_UP
            wrap_response = await _produce_interviewer_message_and_emit_checkpoint()
            self._log(
                f"[Interview] ResumeWrapUp: {wrap_response.state_before}->"
                f"{wrap_response.state_after} "
                f"text='{wrap_response.interviewer_text}'"
            )
            if wrap_response.interviewer_text:
                async for event in self._send_scripted_text(wrap_response.interviewer_text):
                    yield event
            self._emit_interview_completed()
            self._log("[Interview] Resume wrap-up completed")
            return
        if resume_mode == RESUME_MODE_QUESTION_START:
            if flow.state != ASK_QUESTION:
                flow.state = ASK_QUESTION
            resumed_question_response = (
                await _produce_interviewer_message_and_emit_checkpoint()
            )
            self._log(
                f"[Interview] ResumeQuestion: {resumed_question_response.state_before}->"
                f"{resumed_question_response.state_after} "
                f"q={resumed_question_response.question_id} "
                f"text='{resumed_question_response.interviewer_text}'"
            )
            if resumed_question_response.interviewer_text:
                self._activate_context_segment(resumed_question_response.question_id)
                async for event in self._send_scripted_text(
                    resumed_question_response.interviewer_text
                ):
                    yield event
        else:
            # Phase 1: Send intro and first question separately via TTS (no LLM needed)
            intro_response = await _produce_interviewer_message_and_emit_checkpoint()
            self._log(
                f"[Interview] Intro: {intro_response.state_before}->{intro_response.state_after} "
                f"text='{intro_response.interviewer_text}'"
            )
            if intro_response.interviewer_text:
                async for event in self._send_scripted_text(intro_response.interviewer_text):
                    yield event

            first_question_response = (
                await _produce_interviewer_message_and_emit_checkpoint()
            )
            self._log(
                f"[Interview] FirstQuestion: {first_question_response.state_before}->"
                f"{first_question_response.state_after} "
                f"q={first_question_response.question_id} "
                f"text='{first_question_response.interviewer_text}'"
            )
            if first_question_response.interviewer_text:
                first_question_text = first_question_response.interviewer_text
                if flow.total_candidate_turns == 0:
                    sanitized_first_question = self._sanitize_initial_question_text(
                        first_question_text
                    )
                    if sanitized_first_question != first_question_text:
                        self._log(
                            "[Interview] FirstQuestion sanitized: "
                            f"before='{first_question_text}' after='{sanitized_first_question}'"
                        )
                    first_question_text = sanitized_first_question
                self._activate_context_segment(first_question_response.question_id)
                async for event in self._send_scripted_text(first_question_text):
                    yield event
        self._emit_interview_runtime_checkpoint()
        self._log("[Interview] Greeting sent via TTS, waiting for candidate")

        try:
            while True:
                self.asr_last_stream_end_reason = ""
                asr_responses = await self.handle_input_event(inputs)
                async for asr_payload in self.handle_asr_response(asr_responses):
                    if isinstance(asr_payload, SentencePartialRecognizedPayload):
                        yield WebEvent.from_payload(asr_payload)
                        continue

                    asr_recognized = asr_payload
                    self.state = StateInProgress
                    self._start_turn()
                    self._log_turn_event(
                        "turn_recognized_emitted",
                        extra={"sentence_len": len(asr_recognized.sentence or "")},
                    )
                    yield WebEvent.from_payload(asr_recognized)
                    candidate_text = asr_recognized.sentence
                    self._log(f"[Interview] Candidate answer: '{candidate_text}'")
                    if flow.current_question_index < len(flow.questions):
                        current_question_id = flow.questions[flow.current_question_index].question_id
                        self._activate_context_segment(current_question_id)

                    if (
                        self.interview_resume_low_signal_guard_active
                        and self._is_low_signal_candidate_answer(candidate_text)
                    ):
                        normalized_answer = self._normalize_candidate_answer_for_guard(
                            candidate_text
                        )
                        self._log(
                            "[Interview] Resume guard blocked low-signal first answer "
                            f"normalized='{normalized_answer or '-'}'"
                        )
                        self._log_turn_event(
                            "resume_guard_low_signal_ignored",
                            extra={
                                "raw_len": len(candidate_text or ""),
                                "normalized_len": len(normalized_answer),
                            },
                        )
                        async for event in self._send_scripted_text(
                            RESUME_FIRST_TURN_GUARD_REMIND_TEXT
                        ):
                            yield event
                        self.state = StateIdle
                        self._log_turn_latency_breakdown(status="done")
                        self._end_turn()
                        continue
                    if self.interview_resume_low_signal_guard_active:
                        self.interview_resume_low_signal_guard_active = False
                        self._log("[Interview] Resume guard cleared by valid first answer")

                    self._emit_candidate_text(candidate_text)

                    # Add candidate answer to conversation history
                    self._append_history_message("user", candidate_text)

                    # LLM call #1: InterviewJudge evaluates the answer
                    self._log("[Interview] Calling InterviewJudge (LLM #1)...")
                    self._log_turn_event("judge_start")
                    try:
                        answer_response: FlowResponse = await flow.receive_candidate_answer(
                            candidate_text
                        )
                    except Exception as e:
                        self._log_turn_event("judge_error", extra={"error": str(e)})
                        self._log_turn_event("turn_aborted", extra={"stage": "judge"})
                        self._log_turn_latency_breakdown(status="aborted")
                        self._end_turn()
                        raise
                    self._emit_interview_runtime_checkpoint()
                    self._log_turn_event("judge_end")
                    decision = answer_response.decision

                    if decision:
                        self._log(
                            f"[Interview] Judge result: "
                            f"{answer_response.state_before}->{answer_response.state_after} "
                            f"q={answer_response.question_id} "
                            f"next_action={decision.next_action} "
                            f"coverage={decision.coverage_score:.2f} "
                            f"reason={decision.reason} "
                            f"next_prompt='{decision.next_prompt}'"
                        )
                    else:
                        self._log(
                            f"[Interview] Judge result: "
                            f"{answer_response.state_before}->{answer_response.state_after} "
                            f"q={answer_response.question_id} decision=None"
                        )
                    for t in answer_response.transition_trace:
                        self._log(f"[Interview]   transition: {t}")

                    # Check if interview ended (e.g. global turn limit)
                    if flow.is_done:
                        self._log("[Interview] Flow reached DONE after judge, sending wrap-up")
                        wrap_response = (
                            await _produce_interviewer_message_and_emit_checkpoint()
                        )
                        if wrap_response.interviewer_text:
                            self._log(
                                f"[Interview] Wrap-up text: '{wrap_response.interviewer_text}'"
                            )
                            async for event in self._send_scripted_text(
                                wrap_response.interviewer_text
                            ):
                                yield event
                        self.state = StateIdle
                        self._log_turn_latency_breakdown(status="done")
                        self._end_turn()
                        self._emit_interview_completed()
                        self._log("[Interview] Interview ended")
                        return

                    # Get next interviewer message if flow state needs one
                    next_interviewer_text = ""
                    next_response: Optional[FlowResponse] = None
                    if flow.state in (ASK_QUESTION, ASK_FOLLOWUP, ASK_CLARIFY, WRAP_UP):
                        next_response = (
                            await _produce_interviewer_message_and_emit_checkpoint()
                        )
                        next_interviewer_text = next_response.interviewer_text
                        self._log(
                            f"[Interview] Next action: "
                            f"{next_response.state_before}->{next_response.state_after} "
                            f"q={next_response.question_id} "
                            f"text='{next_interviewer_text}'"
                        )

                        if flow.is_done:
                            self._log("[Interview] Flow reached DONE after producing message")
                            async for event in self._send_scripted_text(next_interviewer_text):
                                yield event
                            self.state = StateIdle
                            self._log_turn_latency_breakdown(status="done")
                            self._end_turn()
                            self._emit_interview_completed()
                            self._log("[Interview] Interview ended")
                            return

                    delivery_state = self._resolve_delivery_state(flow.state, next_response)
                    next_question_id = (
                        next_response.question_id if next_response else answer_response.question_id
                    )
                    is_scene_transition = self._compute_scene_transition_for_delivery(
                        decision=decision,
                        delivery_state=delivery_state,
                        current_question_id=answer_response.question_id,
                        next_question_id=next_question_id,
                    )
                    self._activate_context_segment(next_question_id)

                    # LLM call #2: Generate natural interviewer speech
                    clarify_subtype = self._clarify_subtype_from_decision(decision)
                    interview_context = self._build_interview_context(
                        decision=decision,
                        next_question_or_followup=next_interviewer_text,
                        flow_state=delivery_state,
                        has_candidate_speech=flow.total_candidate_turns > 0,
                        is_scene_transition=is_scene_transition,
                    )
                    origin = self._flow_state_origin(delivery_state)
                    prompt_constrained = delivery_state in (ASK_QUESTION, ASK_CLARIFY)
                    question_delivery_mode = "prompt_constrained_paraphrase"
                    segment_id = self.active_context_segment.strip() or "-"
                    segment_context_len = self._calc_segment_context_char_len(interview_context)
                    self._log(
                        f"[Interview] Calling Interviewer LLM (LLM #2) with context:\n{interview_context}"
                    )
                    self._log(
                        "[Interview] LLM #2 context stats: "
                        f"delivery_state={delivery_state} "
                        f"origin={origin} "
                        f"clarify_subtype={clarify_subtype} "
                        f"is_scene_transition={is_scene_transition} "
                        f"question_delivery_mode={question_delivery_mode} "
                        f"prompt_constrained={prompt_constrained} "
                        f"segment_id={segment_id} "
                        f"context_len={len(interview_context)} "
                        f"segment_context_len={segment_context_len}"
                    )
                    self._log_turn_event(
                        "interviewer_llm_start",
                        extra={
                            "context_len": len(interview_context),
                            "segment_context_len": segment_context_len,
                            "segment_id": segment_id,
                            "delivery_state": delivery_state,
                            "origin": origin,
                            "clarify_subtype": clarify_subtype,
                            "is_scene_transition": is_scene_transition,
                            "question_delivery_mode": question_delivery_mode,
                            "prompt_constrained": prompt_constrained,
                        },
                    )

                    try:
                        llm_stream_rsp = self.stream_interview_llm_chat(interview_context)
                        async for payload in self.handle_tts_response(llm_stream_rsp):
                            yield WebEvent.from_payload(payload)
                        self._log("[Interview] Interviewer LLM response sent via TTS")
                    except Exception as e:
                        self._log_turn_event(
                            "interviewer_llm_error", extra={"error": str(e)}
                        )
                        self._log_turn_event(
                            "turn_aborted",
                            extra={
                                "stage": "interviewer_llm_or_tts",
                                "recovered_by": "scripted_fallback",
                            },
                        )
                        self._log(
                            f"[Interview] Main LLM error: {e}, falling back to scripted text"
                        )
                        fallback_text = next_interviewer_text or "好的，我们继续下一个问题。"
                        async for event in self._send_scripted_text(fallback_text):
                            yield event

                    self.state = StateIdle
                    self._log_turn_latency_breakdown(status="done")
                    self._end_turn()
                    self._log(
                        f"[Interview] Turn complete, flow state={flow.state}, waiting for next candidate input"
                    )
                self._log_asr_stream_reset(
                    self.asr_last_stream_end_reason or "upstream_closed"
                )
                await asyncio.sleep(0.05)
        except ASRInitUnavailableError as asr_err:
            self.state = StateIdle
            self._log(f"[Interview] ASR init fatal: {asr_err}")
            self._log_turn_event("turn_aborted", extra={"stage": "asr_init"})
            self._log_turn_latency_breakdown(status="aborted")
            self._end_turn()
            yield self._build_asr_unavailable_event()
            return
        except Exception as e:
            self.state = StateIdle
            self._log_turn_event("turn_aborted", extra={"stage": "unhandled_exception", "error": str(e)})
            self._log_turn_latency_breakdown(status="aborted")
            self._end_turn()
            raise
