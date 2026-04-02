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


def test_build_interview_score_inputs_aggregates_scene_segment_fields():
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
    assert item["question_id"] == "scene-1"
    assert item["ability_dimension"] == "项目管理"
    assert item["score_format"] == "评分0-5"
    assert item["comment_requirement"] == "摘要 + 改进建议"
    assert item["aggregated_answer"] == "我负责拆解目标\n最终提升转化率"
    assert item["question"].startswith("请介绍项目")


def test_build_interview_score_inputs_groups_by_contiguous_scene_segments():
    class _FakeFlow:
        def export_question_answer_snapshots(self):
            return [
                QuestionAnswerSnapshot(
                    question_id="q1",
                    sort_order=1,
                    question="项目背景是什么？",
                    evidence={"scenario": "项目复盘", "scoring_boundary": "关注背景", "score_format": "12"},
                    candidate_answers=["回答1"],
                    aggregated_answer="回答1",
                ),
                QuestionAnswerSnapshot(
                    question_id="q2",
                    sort_order=2,
                    question="你的职责是什么？",
                    evidence={"scenario": "项目复盘"},
                    candidate_answers=["回答2"],
                    aggregated_answer="回答2",
                ),
                QuestionAnswerSnapshot(
                    question_id="q3",
                    sort_order=3,
                    question="如何止血？",
                    evidence={"scenario": "线上故障", "scoring_boundary": "关注排障", "score_format": "10"},
                    candidate_answers=["回答3"],
                    aggregated_answer="回答3",
                ),
                QuestionAnswerSnapshot(
                    question_id="q4",
                    sort_order=4,
                    question="再次出现如何预防？",
                    evidence={"scenario": "线上故障"},
                    candidate_answers=["回答4"],
                    aggregated_answer="回答4",
                ),
                QuestionAnswerSnapshot(
                    question_id="q5",
                    sort_order=5,
                    question="再次谈项目复盘",
                    evidence={"scenario": "项目复盘", "scoring_boundary": "关注复盘深度", "score_format": "8"},
                    candidate_answers=["回答5"],
                    aggregated_answer="回答5",
                ),
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
    assert [item["question_id"] for item in payloads] == ["scene-1", "scene-2", "scene-3"]
    assert payloads[0]["aggregated_answer"] == "回答1\n回答2"
    assert payloads[0]["score_format"] == "12"
    assert payloads[1]["aggregated_answer"] == "回答3\n回答4"
    assert payloads[1]["score_format"] == "10"
    assert payloads[2]["aggregated_answer"] == "回答5"
    assert payloads[2]["score_format"] == "8"


def test_stream_interview_llm_chat_uses_contiguous_scene_context_windows():
    async def _run():
        fake_adapter = _FakeResponsesAdapter(["好的。"])
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            interview_mode=True,
            interview_questions=[
                {"question_id": "q1", "main_question": "Q1", "scenario": "项目复盘"},
                {"question_id": "q2", "main_question": "Q2", "scenario": "项目复盘"},
                {"question_id": "q3", "main_question": "Q3", "scenario": "故障处理"},
            ],
            responses_adapter=fake_adapter,
            session_id="s1",
        )
        await svc.init()

        svc._activate_context_segment("q1")
        svc._append_history_message("user", "A1")
        svc._append_history_message("assistant", "B1")
        svc._activate_context_segment("q2")
        svc._append_history_message("user", "A2")

        svc._activate_context_segment("q3")
        svc._append_history_message("user", "C1")
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("请继续提问"):
            chunks.append(chunk)
        assert "".join(chunks) == "好的。"

        third_scene_call = fake_adapter.calls[-1]
        third_scene_messages = third_scene_call["messages"]
        third_scene_texts = [item["content"] for item in third_scene_messages]
        assert "C1" in third_scene_texts
        assert "A1" not in third_scene_texts
        assert "A2" not in third_scene_texts
        assert third_scene_texts[-1] == "请继续提问"

        svc._activate_context_segment("q2")
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("继续项目问题"):
            chunks.append(chunk)
        assert "".join(chunks) == "好的。"
        project_scene_call = fake_adapter.calls[-1]
        project_scene_texts = [item["content"] for item in project_scene_call["messages"]]
        assert "A1" in project_scene_texts
        assert "B1" in project_scene_texts
        assert "A2" in project_scene_texts
        assert "C1" not in project_scene_texts
        assert project_scene_texts[-1] == "继续项目问题"

    asyncio.run(_run())


def test_stream_interview_llm_chat_treats_empty_scene_as_per_question_window():
    async def _run():
        fake_adapter = _FakeResponsesAdapter(["收到。"])
        svc = service.VoiceBotService(
            ark_api_key="ark-key",
            llm1_endpoint_id="ep-judge",
            llm2_endpoint_id="ep-interviewer",
            asr_app_key="asr-app",
            asr_access_key="asr-token",
            tts_app_key="tts-app",
            tts_access_key="tts-token",
            interview_mode=True,
            interview_questions=[
                {"question_id": "q1", "main_question": "Q1", "scenario": ""},
                {"question_id": "q2", "main_question": "Q2", "scenario": ""},
            ],
            responses_adapter=fake_adapter,
            session_id="s2",
        )
        await svc.init()

        svc._activate_context_segment("q1")
        svc._append_history_message("user", "Q1-answer")
        svc._activate_context_segment("q2")
        svc._append_history_message("user", "Q2-answer")
        chunks = []
        async for chunk in svc.stream_interview_llm_chat("继续第二题"):
            chunks.append(chunk)
        assert "".join(chunks) == "收到。"

        call = fake_adapter.calls[-1]
        texts = [item["content"] for item in call["messages"]]
        assert "Q2-answer" in texts
        assert "Q1-answer" not in texts
        assert texts[-1] == "继续第二题"

    asyncio.run(_run())
