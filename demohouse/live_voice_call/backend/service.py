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
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterable, Callable, Dict, List, Optional, Union

from arkitect.core.component.asr import ASRFullServerResponse, AsyncASRClient
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
    ASK_FOLLOWUP,
    ASK_QUESTION,
    DONE,
    INTRO,
    WAIT_ANSWER,
    WRAP_UP,
    FlowResponse,
    InterviewFlow,
)
from interview_judge import Decision, InterviewJudge
from prompt import InterviewerPrompt, VoiceBotPrompt
from llm_limiter import llm_slot

StateInProgress = "InProgress"
StateIdle = "Idle"
# asr continuous detection no input duration, empirical value
ASRInterval = 2000
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
    asr_client: Optional[AsyncASRClient] = None
    tts_client: Optional[AsyncTTSClient] = None
    llm_ep_id: str
    state: str = StateIdle
    tts_speaker: str = DEFAULT_SPEAKER  # TTS live_voice_call

    """
    config vars
    """
    asr_app_key: str
    asr_access_key: str
    tts_app_key: str
    tts_access_key: str

    interview_mode: bool = False
    interview_flow: Optional[Any] = None
    interview_judge: Optional[Any] = None
    interview_questions: Optional[List[Dict[str, Any]]] = None
    on_candidate_sentence: Optional[Callable[[str], None]] = None
    on_bot_sentence: Optional[Callable[[str], None]] = None
    on_bot_audio_chunk: Optional[Callable[[bytes], None]] = None
    on_interview_completed: Optional[Callable[[], None]] = None
    log_fn: Optional[Callable[[str], None]] = None
    session_id: str = ""

    history_messages: List[ArkMessage] = []  # Store historical dialogue information

    asr_buffer: str = ""  # Reservoir asr recognition result
    asr_no_input_duration: int = 0  # Cumulated no live_voice_call recognition duration
    asr_last_duration: int = 0  # Last asr recognition duration
    asr_init_count: int = 0  # Count actual asr_client.init() calls per service instance
    asr_init_failure_streak: int = 0  # Consecutive init failures since last success
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
        self.asr_client = AsyncASRClient(
            app_key=self.asr_app_key, access_key=self.asr_access_key
        )
        # await self.tts_client.init() # 这里也不需要，因为 lazy init 会按需建立连接。
        # await self.asr_client.init() # 这里有问题，需要comment 掉才可以正常使用。因为是 AI 先说话，这时初始化 client 会导致 server 挂起。


        if self.interview_mode:
            flow_questions = self.interview_questions or DEFAULT_INTERVIEW_QUESTIONS
            self.interview_judge = InterviewJudge(
                llm_endpoint_id=self.llm_ep_id,
                max_followups_per_question=1,
                coverage_threshold=0.7,
            )
            self.interview_flow = InterviewFlow(
                questions=flow_questions,
                judge=self.interview_judge,
                max_followups_per_question=1,
                global_turn_limit=20,
            )

    def _log(self, message: str) -> None:
        if self.log_fn:
            try:
                self.log_fn(message)
                return
            except Exception as log_err:
                INFO(f"[VoiceBotService] custom log_fn failed: {log_err}")
        INFO(message)

    def _wall_time_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _mono_ms(self) -> int:
        return int(round(time.monotonic() * 1000))

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
        self.history_messages.append(
            ArkMessage(**{"role": "assistant", "content": GREETING_TEXT})
        )
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

        asr_responses = await self.handle_input_event(inputs)
        try:
            async for asr_recognized in self.handle_asr_response(asr_responses):
                # set state into InProgress
                self.state = StateInProgress
                yield WebEvent.from_payload(asr_recognized)
                llm_stream_rsp = self.stream_llm_chat(asr_recognized.sentence)
                async for payload in self.handle_tts_response(llm_stream_rsp):
                    yield WebEvent.from_payload(payload)
                # recreate the asr and tts client
                self.state = StateIdle
        except ASRInitUnavailableError as asr_err:
            self.state = StateIdle
            self._log(f"ASR_INIT_FATAL error={asr_err}")
            yield self._build_asr_unavailable_event()
            return

    async def handle_input_event(
        self, inputs: AsyncIterable[WebEvent]
    ) -> AsyncIterable[ASRFullServerResponse]:
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
                elif input_event.event == USER_AUDIO and input_event.data:
                    yield input_event.data

        asr_stream = self.asr_client.stream_asr(async_gen())

        async def merged_stream() -> AsyncIterable[ASRFullServerResponse]:
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
                    break
                yield rsp

        return merged_stream()

    async def handle_asr_response(
        self, asr_responses: AsyncIterable[ASRFullServerResponse]
    ) -> AsyncIterable[SentenceRecognizedPayload]:
        """
        Handle ASR responses and generate recognized sentences.
        """
        async for response in asr_responses:
            if self.state == StateIdle:
                if self.asr_buffer and self.asr_no_input_duration > ASRInterval:
                    yield SentenceRecognizedPayload(sentence=self.asr_buffer)
                    self.asr_buffer = ""
                    self.asr_no_input_duration = 0
                    self.asr_last_duration = 0
                    await self.asr_client.close()
                elif response.result and response.result.text:
                    # buffering
                    increment_len = len(response.result.text) - len(self.asr_buffer)
                    self.asr_buffer = response.result.text
                    if increment_len > 0:
                        self.asr_last_duration = response.audio.duration
                    else:
                        self.asr_no_input_duration = (
                            response.audio.duration - self.asr_last_duration
                        )
                    self._log(
                        f"asr buffer incremented: {increment_len}, utterances: {response.result.utterances}"
                    )
            else:
                self._log("service is InProgress, will ignore the newer asr response")
                continue

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
        self.history_messages.append(ArkMessage(**{"role": "user", "content": text}))

        llm = BaseChatLanguageModel(
            template=VoiceBotPrompt(),
            messages=self.history_messages,
            endpoint_id=self.llm_ep_id,
        )
        completion_buffer = ""

        async with llm_slot():
            async for chunk in llm.astream():
                if chunk.choices and chunk.choices[0].delta:
                    yield chunk.choices[0].delta.content
                    completion_buffer += chunk.choices[0].delta.content

        if completion_buffer:
            self.history_messages.append(
                ArkMessage(**{"role": "assistant", "content": completion_buffer})
            )
            self._emit_bot_text(completion_buffer)

    async def _send_scripted_text(self, text: str) -> AsyncIterable[WebEvent]:
        """Send a pre-determined text string through TTS without an LLM call."""
        text_stream = self._greeting_text_stream(text)
        async for payload in self.handle_tts_response(text_stream):
            yield WebEvent.from_payload(payload)
        self.history_messages.append(
            ArkMessage(**{"role": "assistant", "content": text})
        )
        self._emit_bot_text(text)

    def _build_interview_context(
        self,
        decision: Optional[Decision],
        next_question_or_followup: str,
        flow_state: str,
    ) -> str:
        """Build the interview context string for the main interviewer LLM."""
        parts = []

        if decision:
            parts.append(f"[评估结果] 评判理由: {decision.reason}")
            parts.append(f"[评估结果] 覆盖度得分: {decision.coverage_score:.2f}")

            if decision.move_forward:
                parts.append("[指令] 候选人回答充分，请先简要肯定，然后自然过渡到下一个问题。")
            elif decision.need_follow_up:
                parts.append("[指令] 候选人回答不够完整，请礼貌地进行追问以获取更多细节。")
                if decision.follow_up_question:
                    parts.append(f"[追问方向] {decision.follow_up_question}")

        if next_question_or_followup:
            parts.append(f"[下一步内容] {next_question_or_followup}")

        if flow_state == WRAP_UP:
            parts.append("[指令] 面试即将结束，请做结束语。")

        return "\n".join(parts)

    async def stream_interview_llm_chat(
        self, interview_context: str
    ) -> AsyncIterable[str]:
        """Stream the main interviewer LLM response (LLM call #2).

        Uses InterviewerPrompt. The interview_context is passed as an
        ephemeral user message but NOT persisted in history_messages.
        The LLM's output IS added to history.
        """
        messages_for_llm = self.history_messages + [
            ArkMessage(**{"role": "user", "content": interview_context})
        ]

        llm = BaseChatLanguageModel(
            template=InterviewerPrompt(),
            messages=messages_for_llm,
            endpoint_id=self.llm_ep_id,
        )
        completion_buffer = ""
        first_token_logged = False

        async with llm_slot():
            async for chunk in llm.astream():
                if chunk.choices and chunk.choices[0].delta:
                    delta_text = chunk.choices[0].delta.content
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
            self.history_messages.append(
                ArkMessage(**{"role": "assistant", "content": completion_buffer})
            )
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
        self._log("[Interview] Starting interview handler loop")

        # Phase 1: Send intro and first question separately via TTS (no LLM needed)
        intro_response = await flow.produce_interviewer_message()
        self._log(
            f"[Interview] Intro: {intro_response.state_before}->{intro_response.state_after} "
            f"text='{intro_response.interviewer_text}'"
        )
        if intro_response.interviewer_text:
            async for event in self._send_scripted_text(intro_response.interviewer_text):
                yield event

        first_question_response = await flow.produce_interviewer_message()
        self._log(
            f"[Interview] FirstQuestion: {first_question_response.state_before}->"
            f"{first_question_response.state_after} "
            f"q={first_question_response.question_id} "
            f"text='{first_question_response.interviewer_text}'"
        )
        if first_question_response.interviewer_text:
            async for event in self._send_scripted_text(
                first_question_response.interviewer_text
            ):
                yield event
        self._log("[Interview] Greeting sent via TTS, waiting for candidate")

        # Phase 2: Main interview loop
        asr_responses = await self.handle_input_event(inputs)
        try:
            async for asr_recognized in self.handle_asr_response(asr_responses):
                self.state = StateInProgress
                self._start_turn()
                self._log_turn_event(
                    "turn_recognized_emitted",
                    extra={"sentence_len": len(asr_recognized.sentence or "")},
                )
                yield WebEvent.from_payload(asr_recognized)
                candidate_text = asr_recognized.sentence
                self._log(f"[Interview] Candidate answer: '{candidate_text}'")
                self._emit_candidate_text(candidate_text)

                # Add candidate answer to conversation history
                self.history_messages.append(
                    ArkMessage(**{"role": "user", "content": candidate_text})
                )

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
                self._log_turn_event("judge_end")
                decision = answer_response.decision

                if decision:
                    self._log(
                        f"[Interview] Judge result: "
                        f"{answer_response.state_before}->{answer_response.state_after} "
                        f"q={answer_response.question_id} "
                        f"move_forward={decision.move_forward} "
                        f"need_follow_up={decision.need_follow_up} "
                        f"coverage={decision.coverage_score:.2f} "
                        f"reason={decision.reason}"
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
                    wrap_response = await flow.produce_interviewer_message()
                    if wrap_response.interviewer_text:
                        self._log(f"[Interview] Wrap-up text: '{wrap_response.interviewer_text}'")
                        async for event in self._send_scripted_text(
                            wrap_response.interviewer_text
                        ):
                            yield event
                    self.state = StateIdle
                    self._log_turn_latency_breakdown(status="done")
                    self._end_turn()
                    self._emit_interview_completed()
                    self._log("[Interview] Interview ended")
                    break

                # Get next interviewer message if flow state needs one
                next_interviewer_text = ""
                if flow.state in (ASK_QUESTION, ASK_FOLLOWUP, WRAP_UP):
                    next_response = await flow.produce_interviewer_message()
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
                        break

                # LLM call #2: Generate natural interviewer speech
                interview_context = self._build_interview_context(
                    decision=decision,
                    next_question_or_followup=next_interviewer_text,
                    flow_state=flow.state,
                )
                self._log(f"[Interview] Calling Interviewer LLM (LLM #2) with context:\n{interview_context}")
                self._log_turn_event(
                    "interviewer_llm_start",
                    extra={"context_len": len(interview_context)},
                )

                try:
                    llm_stream_rsp = self.stream_interview_llm_chat(interview_context)
                    async for payload in self.handle_tts_response(llm_stream_rsp):
                        yield WebEvent.from_payload(payload)
                    self._log("[Interview] Interviewer LLM response sent via TTS")
                except Exception as e:
                    self._log_turn_event("interviewer_llm_error", extra={"error": str(e)})
                    self._log_turn_event(
                        "turn_aborted",
                        extra={"stage": "interviewer_llm_or_tts", "recovered_by": "scripted_fallback"},
                    )
                    self._log(f"[Interview] Main LLM error: {e}, falling back to scripted text")
                    fallback_text = next_interviewer_text or "好的，我们继续下一个问题。"
                    async for event in self._send_scripted_text(fallback_text):
                        yield event

                self.state = StateIdle
                self._log_turn_latency_breakdown(status="done")
                self._end_turn()
                self._log(f"[Interview] Turn complete, flow state={flow.state}, waiting for next candidate input")
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
