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

from typing import Any, AsyncIterable, Dict, List, Optional, Union

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

StateInProgress = "InProgress"
StateIdle = "Idle"
# asr continuous detection no input duration, empirical value
ASRInterval = 2000
# Default tts live_voice_call
DEFAULT_SPEAKER = "zh_female_sajiaonvyou_moon_bigtts"
# Greeting message spoken by the bot before the user starts
GREETING_TEXT = "你好，欢迎参加今天的面试。请先做一个简短的自我介绍吧。"

INTERVIEW_QUESTIONS: List[Dict[str, Any]] = [
    {
        "question_id": "q1",
        "main_question": "请用1分钟做自我介绍，重点说与你申请岗位相关的经历。",
        "evidence": {"must_cover": ["经历", "岗位", "优势"]},
    },
    {
        "question_id": "q2",
        "main_question": "请介绍一个你主导的项目，说明目标、你的动作和结果。",
        "evidence": {"must_cover": ["目标", "动作", "结果"]},
    },
    {
        "question_id": "q3",
        "main_question": "当你遇到复杂问题时，通常如何拆解并推动解决？",
        "evidence": {"must_cover": ["拆解", "推动", "复盘"]},
    },
]


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

    history_messages: List[ArkMessage] = []  # Store historical dialogue information

    asr_buffer: str = ""  # Reservoir asr recognition result
    asr_no_input_duration: int = 0  # Cumulated no live_voice_call recognition duration
    asr_last_duration: int = 0  # Last asr recognition duration

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
        await self.tts_client.init()

        if self.interview_mode:
            self.interview_judge = InterviewJudge(
                llm_endpoint_id=self.llm_ep_id,
                max_followups_per_question=2,
                coverage_threshold=0.7,
            )
            self.interview_flow = InterviewFlow(
                questions=INTERVIEW_QUESTIONS,
                judge=self.interview_judge,
                max_followups_per_question=2,
                global_turn_limit=20,
            )

    async def _greeting_text_stream(self, text: str) -> AsyncIterable[str]:
        """Yield a single greeting text for TTS processing (no LLM call)."""
        yield text

    async def send_greeting(self) -> AsyncIterable[WebEvent]:
        """Send an initial greeting through TTS before the conversation starts."""
        greeting_stream = self._greeting_text_stream(GREETING_TEXT)
        async for payload in self.handle_tts_response(greeting_stream):
            yield WebEvent.from_payload(payload)
        self.history_messages.append(
            ArkMessage(**{"role": "assistant", "content": GREETING_TEXT})
        )

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
        async for asr_recognized in self.handle_asr_response(asr_responses):
            # set state into InProgress
            self.state = StateInProgress
            yield WebEvent.from_payload(asr_recognized)
            llm_stream_rsp = self.stream_llm_chat(asr_recognized.sentence)
            async for payload in self.handle_tts_response(llm_stream_rsp):
                yield WebEvent.from_payload(payload)
            # recreate the asr and tts client
            self.state = StateIdle

    async def handle_input_event(
        self, inputs: AsyncIterable[WebEvent]
    ) -> AsyncIterable[ASRFullServerResponse]:
        """
        Handle input events and generate ASR responses.
        """

        async def async_gen() -> AsyncIterable[bytes]:
            async for input_event in inputs:
                if self.state != StateIdle:
                    INFO("service is InProgress, will ignore the incoming input")
                    continue
                elif not self.asr_client.inited:
                    INFO("need recreate asr conn")
                    await self.asr_client.init()

                INFO(
                    f"receive input, event={input_event.event} payload={input_event.payload}"
                )
                if input_event.event == BOT_UPDATE_CONFIG and isinstance(
                    input_event.payload, BotUpdateConfigPayload
                ):
                    self.tts_speaker = input_event.payload.speaker
                elif input_event.event == USER_AUDIO and input_event.data:
                    yield input_event.data

        return self.asr_client.stream_asr(async_gen())

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
                    INFO(
                        f"asr buffer incremented: {increment_len}, utterances: {response.result.utterances}"
                    )
            else:
                INFO("service is InProgress, will ignore the newer asr response")
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
            INFO("need recreate tts client")
            await self.tts_client.init()
        async for tts_rsp in self.tts_client.tts(
            source=llm_output, include_transcript=True
        ):
            INFO(
                f"receive tts response: event={tts_rsp.event} transcript={tts_rsp.transcript} \
                audio len={len(tts_rsp.audio) if tts_rsp.audio else 0}"
            )
            if tts_rsp.event == EventTTSSentenceStart:
                yield TTSSentenceStartPayload(sentence=tts_rsp.transcript)
            elif tts_rsp.event == EventTTSSentenceEnd:
                yield TTSSentenceEndPayload(data=buffer)
                buffer.clear()
            elif tts_rsp.audio:
                buffer.extend(tts_rsp.audio)

            if tts_rsp.event == EventSessionFinished:
                yield TTSDonePayload()
                await self.tts_client.close()
                break

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

        async for chunk in llm.astream():
            if chunk.choices and chunk.choices[0].delta:
                yield chunk.choices[0].delta.content
                completion_buffer += chunk.choices[0].delta.content

        if completion_buffer:
            self.history_messages.append(
                ArkMessage(**{"role": "assistant", "content": completion_buffer})
            )

    async def _send_scripted_text(self, text: str) -> AsyncIterable[WebEvent]:
        """Send a pre-determined text string through TTS without an LLM call."""
        text_stream = self._greeting_text_stream(text)
        async for payload in self.handle_tts_response(text_stream):
            yield WebEvent.from_payload(payload)
        self.history_messages.append(
            ArkMessage(**{"role": "assistant", "content": text})
        )

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

        async for chunk in llm.astream():
            if chunk.choices and chunk.choices[0].delta:
                yield chunk.choices[0].delta.content
                completion_buffer += chunk.choices[0].delta.content

        if completion_buffer:
            self.history_messages.append(
                ArkMessage(**{"role": "assistant", "content": completion_buffer})
            )

    async def _interview_handler_loop(
        self, inputs: AsyncIterable[WebEvent]
    ) -> AsyncIterable[WebEvent]:
        """Interview-mode main loop using InterviewFlow state machine."""
        flow = self.interview_flow
        INFO("[Interview] Starting interview handler loop")

        # Phase 1: Send intro + first question via TTS (no LLM needed)
        intro_response = await flow.produce_interviewer_message()
        INFO(
            f"[Interview] Intro: {intro_response.state_before}->{intro_response.state_after} "
            f"text='{intro_response.interviewer_text}'"
        )

        first_question_response = await flow.produce_interviewer_message()
        INFO(
            f"[Interview] FirstQuestion: {first_question_response.state_before}->"
            f"{first_question_response.state_after} "
            f"q={first_question_response.question_id} "
            f"text='{first_question_response.interviewer_text}'"
        )

        greeting_text = (
            intro_response.interviewer_text + " " + first_question_response.interviewer_text
        )
        greeting_stream = self._greeting_text_stream(greeting_text)
        async for payload in self.handle_tts_response(greeting_stream):
            yield WebEvent.from_payload(payload)

        self.history_messages.append(
            ArkMessage(**{"role": "assistant", "content": greeting_text})
        )
        INFO("[Interview] Greeting sent via TTS, waiting for candidate")

        # Phase 2: Main interview loop
        asr_responses = await self.handle_input_event(inputs)
        async for asr_recognized in self.handle_asr_response(asr_responses):
            self.state = StateInProgress
            yield WebEvent.from_payload(asr_recognized)
            candidate_text = asr_recognized.sentence
            INFO(f"[Interview] Candidate answer: '{candidate_text}'")

            # Add candidate answer to conversation history
            self.history_messages.append(
                ArkMessage(**{"role": "user", "content": candidate_text})
            )

            # LLM call #1: InterviewJudge evaluates the answer
            INFO("[Interview] Calling InterviewJudge (LLM #1)...")
            answer_response: FlowResponse = await flow.receive_candidate_answer(
                candidate_text
            )
            decision = answer_response.decision

            INFO(
                f"[Interview] Judge result: "
                f"{answer_response.state_before}->{answer_response.state_after} "
                f"q={answer_response.question_id} "
                f"move_forward={decision.move_forward if decision else None} "
                f"need_follow_up={decision.need_follow_up if decision else None} "
                f"coverage={decision.coverage_score:.2f if decision else 0} "
                f"reason={decision.reason if decision else 'none'}"
            )
            for t in answer_response.transition_trace:
                INFO(f"[Interview]   transition: {t}")

            # Check if interview ended (e.g. global turn limit)
            if flow.is_done:
                INFO("[Interview] Flow reached DONE after judge, sending wrap-up")
                wrap_response = await flow.produce_interviewer_message()
                if wrap_response.interviewer_text:
                    INFO(f"[Interview] Wrap-up text: '{wrap_response.interviewer_text}'")
                    async for event in self._send_scripted_text(
                        wrap_response.interviewer_text
                    ):
                        yield event
                self.state = StateIdle
                INFO("[Interview] Interview ended")
                break

            # Get next interviewer message if flow state needs one
            next_interviewer_text = ""
            if flow.state in (ASK_QUESTION, ASK_FOLLOWUP, WRAP_UP):
                next_response = await flow.produce_interviewer_message()
                next_interviewer_text = next_response.interviewer_text
                INFO(
                    f"[Interview] Next action: "
                    f"{next_response.state_before}->{next_response.state_after} "
                    f"q={next_response.question_id} "
                    f"text='{next_interviewer_text}'"
                )

                if flow.is_done:
                    INFO("[Interview] Flow reached DONE after producing message")
                    async for event in self._send_scripted_text(next_interviewer_text):
                        yield event
                    self.state = StateIdle
                    INFO("[Interview] Interview ended")
                    break

            # LLM call #2: Generate natural interviewer speech
            interview_context = self._build_interview_context(
                decision=decision,
                next_question_or_followup=next_interviewer_text,
                flow_state=flow.state,
            )
            INFO(f"[Interview] Calling Interviewer LLM (LLM #2) with context:\n{interview_context}")

            try:
                llm_stream_rsp = self.stream_interview_llm_chat(interview_context)
                async for payload in self.handle_tts_response(llm_stream_rsp):
                    yield WebEvent.from_payload(payload)
                INFO("[Interview] Interviewer LLM response sent via TTS")
            except Exception as e:
                INFO(f"[Interview] Main LLM error: {e}, falling back to scripted text")
                fallback_text = next_interviewer_text or "好的，我们继续下一个问题。"
                async for event in self._send_scripted_text(fallback_text):
                    yield event

            self.state = StateIdle
            INFO(f"[Interview] Turn complete, flow state={flow.state}, waiting for next candidate input")
