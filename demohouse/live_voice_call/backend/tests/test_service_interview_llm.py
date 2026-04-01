import asyncio

from arkitect.core.component.llm.model import ArkMessage

from interview_flow import QuestionAnswerSnapshot
import service


class _FakeResponsesAdapter:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def stream_text(self, **kwargs):
        self.calls.append(kwargs)
        for chunk in self.chunks:
            yield chunk


def test_stream_interview_llm_chat_uses_responses_adapter_and_persists_history():
    async def _run():
        emitted = []
        fake_adapter = _FakeResponsesAdapter(["你好", "，请继续。"])
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            llm2_thinking_type="enabled",
            llm2_reasoning_effort="low",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            responses_adapter=fake_adapter,
            on_bot_sentence=emitted.append,
            session_id="s1",
        )
        svc.history_messages = [
            ArkMessage(**{"role": "assistant", "content": "上一轮问题"})
        ]
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("请追问一个细节"):
            chunks.append(chunk)

        assert "".join(chunks) == "你好，请继续。"
        assert emitted[-1] == "你好，请继续。"
        assert fake_adapter.calls
        call = fake_adapter.calls[0]
        assert call["model"] == "ep-interviewer"
        assert call["thinking_type"] == "enabled"
        assert call["reasoning_effort"] == "low"
        assert svc.history_messages[-1].role == "assistant"
        assert svc.history_messages[-1].content == "你好，请继续。"

    asyncio.run(_run())


def test_build_interview_score_inputs_maps_snapshot_fields():
    class _FakeFlow:
        def export_question_answer_snapshots(self):
            return [
                QuestionAnswerSnapshot(
                    question_id="q1",
                    sort_order=1,
                    question="请介绍项目",
                    evidence={
                        "ability_dimension": "项目管理",
                        "scoring_boundary": "目标-行动-结果",
                        "best_standard": "完整闭环",
                        "medium_standard": "部分闭环",
                        "worst_standard": "无结果",
                        "score_format": "评分0-5",
                        "comment_requirement": "摘要 + 改进建议",
                    },
                    candidate_answers=["我负责拆解目标", "最终提升转化率"],
                    aggregated_answer="我负责拆解目标\\n最终提升转化率",
                )
            ]

    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        interview_mode=True,
    )
    svc.interview_flow = _FakeFlow()
    payloads = svc.build_interview_score_inputs()
    assert len(payloads) == 1
    item = payloads[0]
    assert item["question_id"] == "q1"
    assert item["ability_dimension"] == "项目管理"
    assert item["score_format"] == "评分0-5"
    assert item["comment_requirement"] == "摘要 + 改进建议"
    assert item["aggregated_answer"] == "我负责拆解目标\\n最终提升转化率"
