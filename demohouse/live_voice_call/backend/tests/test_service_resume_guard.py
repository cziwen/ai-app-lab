import asyncio
from types import SimpleNamespace

from interview_flow import ASK_QUESTION, WAIT_ANSWER, FlowResponse
import service


class _FakeFlow:
    def __init__(self):
        self.state = WAIT_ANSWER
        self.current_question_index = 0
        self.questions = [SimpleNamespace(question_id="q1")]
        self.total_candidate_turns = 1
        self.is_done = False
        self.received_answers = []

    async def produce_interviewer_message(self):
        if self.state == ASK_QUESTION:
            self.state = WAIT_ANSWER
            return FlowResponse(
                state_before=ASK_QUESTION,
                state_after=WAIT_ANSWER,
                interviewer_text="请继续回答这个问题。",
                decision=None,
                question_id="q1",
                transition_trace=["ASK_QUESTION -> WAIT_ANSWER"],
            )
        return FlowResponse(
            state_before=self.state,
            state_after=self.state,
            interviewer_text="",
            decision=None,
            question_id="q1",
            transition_trace=[],
        )

    async def receive_candidate_answer(self, answer: str):
        self.received_answers.append(answer)
        raise AssertionError("resume guard should block low-signal answer before judge")


def test_is_low_signal_candidate_answer_detects_filler():
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
    )

    assert svc._is_low_signal_candidate_answer("嗯") is True
    assert svc._is_low_signal_candidate_answer(" 嗯... ") is True
    assert svc._is_low_signal_candidate_answer("然后") is True
    assert svc._is_low_signal_candidate_answer("我会先拆解目标，再给出执行计划和结果") is False


def test_resume_guard_blocks_low_signal_answer_before_judge(monkeypatch):
    async def _run():
        sent_texts = []
        emitted_candidate_sentences = []
        flow = _FakeFlow()
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            interview_mode=True,
            on_candidate_sentence=lambda text: emitted_candidate_sentences.append(text),
        )
        svc.interview_flow = flow
        svc.interview_resume_mode = service.RESUME_MODE_QUESTION_START
        svc.interview_resume_low_signal_guard_active = True

        async def _fake_send_scripted_text(_self, text):
            sent_texts.append(text)
            yield service.WebEvent.from_payload(service.TTSDonePayload())

        async def _fake_handle_input_event(_self, _inputs):
            async def _empty():
                if False:
                    yield None

            return _empty()

        async def _fake_handle_asr_response(_self, _responses):
            yield service.SentenceRecognizedPayload(sentence="嗯")
            await asyncio.sleep(3600)

        monkeypatch.setattr(
            service.VoiceBotService,
            "_send_scripted_text",
            _fake_send_scripted_text,
            raising=False,
        )
        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_input_event",
            _fake_handle_input_event,
            raising=False,
        )
        monkeypatch.setattr(
            service.VoiceBotService,
            "handle_asr_response",
            _fake_handle_asr_response,
            raising=False,
        )

        async def _inputs():
            if False:
                yield None

        out_iter = svc._interview_handler_loop(_inputs()).__aiter__()
        first = await asyncio.wait_for(out_iter.__anext__(), timeout=0.3)
        second = await asyncio.wait_for(out_iter.__anext__(), timeout=0.3)
        third = await asyncio.wait_for(out_iter.__anext__(), timeout=0.3)

        assert first.event == service.TTS_DONE
        assert second.event == service.SENTENCE_RECOGNIZED
        assert third.event == service.TTS_DONE
        assert sent_texts == [
            "请继续回答这个问题。",
            service.RESUME_FIRST_TURN_GUARD_REMIND_TEXT,
        ]
        assert flow.received_answers == []
        assert emitted_candidate_sentences == []
        assert svc.interview_resume_low_signal_guard_active is True

    asyncio.run(_run())
