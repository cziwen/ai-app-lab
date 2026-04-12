import asyncio
from dataclasses import dataclass
from typing import List

from interview_flow import (
    ASK_CLARIFY,
    ASK_FOLLOWUP,
    ASK_QUESTION,
    DONE,
    WAIT_ANSWER,
    WRAP_UP,
    InterviewFlow,
)
from interview_judge import Decision


QUESTIONS = [
    {
        "question_id": "q1",
        "main_question": "请做一个简短自我介绍。",
        "max_followups": 2,
        "max_clarifies": 2,
    },
    {
        "question_id": "q2",
        "main_question": "介绍一个你主导的项目。",
        "max_followups": 2,
        "max_clarifies": 2,
    },
    {
        "question_id": "q3",
        "main_question": "讲讲你如何处理复杂问题。",
        "max_followups": 2,
        "max_clarifies": 2,
    },
]


@dataclass
class SequenceJudge:
    decisions: List[Decision]

    async def decide(
        self,
        question,
        candidate_answer,
        follow_up_count,
        clarify_count,
        evidence=None,
    ):
        if not self.decisions:
            raise RuntimeError("no more decisions")
        return self.decisions.pop(0)


async def _bootstrap_to_wait(flow: InterviewFlow):
    if flow.state == "INTRO":
        await flow.produce_interviewer_message()
    if flow.state == ASK_QUESTION:
        await flow.produce_interviewer_message()
    elif flow.state == ASK_FOLLOWUP:
        await flow.produce_interviewer_message()
    elif flow.state == ASK_CLARIFY:
        await flow.produce_interviewer_message()
    assert flow.state == WAIT_ANSWER


def test_happy_path_all_move_forward():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
            ]
        )
        flow = InterviewFlow(questions=QUESTIONS, judge=judge)

        for _ in range(3):
            await _bootstrap_to_wait(flow)
            resp = await flow.receive_candidate_answer("这是一个比较完整的回答，包含行动和结果。")
            assert resp.decision is not None

        assert flow.state == WRAP_UP
        final_msg = await flow.produce_interviewer_message()
        assert final_msg.state_after == DONE

    asyncio.run(_run())


def test_followup_loop_then_move_forward():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("follow_up", "请补充你的职责", "need_more", 0.3),
                Decision("next_question", "", "enough", 0.8),
                Decision("next_question", "", "ok", 0.8),
                Decision("next_question", "", "ok", 0.8),
            ]
        )
        flow = InterviewFlow(questions=QUESTIONS, judge=judge)

        await _bootstrap_to_wait(flow)
        first = await flow.receive_candidate_answer("我做过项目")
        assert first.state_after == ASK_FOLLOWUP

        await _bootstrap_to_wait(flow)
        second = await flow.receive_candidate_answer("我负责拆解需求并推进上线")
        assert second.state_after == ASK_QUESTION

    asyncio.run(_run())


def test_clarify_loop_then_move_forward():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("clarify", "我补充一下题意：请重点说你的个人行动和结果。", "clarify", 0.2),
                Decision("next_question", "", "enough", 0.8),
                Decision("next_question", "", "ok", 0.8),
                Decision("next_question", "", "ok", 0.8),
            ]
        )
        flow = InterviewFlow(questions=QUESTIONS, judge=judge)

        await _bootstrap_to_wait(flow)
        first = await flow.receive_candidate_answer("这题我没听懂")
        assert first.state_after == ASK_CLARIFY

        await _bootstrap_to_wait(flow)
        second = await flow.receive_candidate_answer("我负责拆解需求并推进上线")
        assert second.state_after == ASK_QUESTION

    asyncio.run(_run())


def test_max_followups_forces_move_forward():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("follow_up", "再具体一点", "mock", 0.1),
                Decision("follow_up", "还是不够具体", "mock", 0.1),
                Decision("follow_up", "请补充结果", "mock", 0.1),
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
            ]
        )
        flow = InterviewFlow(questions=QUESTIONS, judge=judge)

        await _bootstrap_to_wait(flow)
        r1 = await flow.receive_candidate_answer("回答一：有点泛")
        assert r1.state_after == ASK_FOLLOWUP

        await _bootstrap_to_wait(flow)
        r2 = await flow.receive_candidate_answer("回答二：还是泛")
        assert r2.state_after == ASK_FOLLOWUP

        await _bootstrap_to_wait(flow)
        r3 = await flow.receive_candidate_answer("回答三：仍然一般")
        assert r3.state_after == ASK_QUESTION
        assert r3.decision is not None
        assert r3.decision.reason == "follow_up_limit_reached"

    asyncio.run(_run())


def test_max_clarifies_forces_move_forward():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("clarify", "这题是想了解你的真实经历。", "mock", 0.1),
                Decision("clarify", "继续澄清", "mock", 0.1),
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
            ]
        )
        one_question = [
            {
                "question_id": "q1",
                "main_question": "请做一个简短自我介绍。",
                "max_followups": 2,
                "max_clarifies": 1,
            },
            {
                "question_id": "q2",
                "main_question": "介绍一个你主导的项目。",
                "max_followups": 2,
                "max_clarifies": 2,
            },
        ]
        flow = InterviewFlow(questions=one_question, judge=judge)

        await _bootstrap_to_wait(flow)
        r1 = await flow.receive_candidate_answer("这题我不太理解")
        assert r1.state_after == ASK_CLARIFY

        await _bootstrap_to_wait(flow)
        r2 = await flow.receive_candidate_answer("我还是没懂")
        assert r2.state_after == ASK_QUESTION
        assert r2.decision is not None
        assert r2.decision.reason == "clarify_limit_reached"

    asyncio.run(_run())


def test_last_question_wraps_up():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
            ]
        )
        flow = InterviewFlow(questions=QUESTIONS, judge=judge)

        for _ in range(3):
            await _bootstrap_to_wait(flow)
            await flow.receive_candidate_answer("完整回答，覆盖关键点。")

        assert flow.state == WRAP_UP

    asyncio.run(_run())


def test_global_turn_limit_forces_wrap_up():
    async def _run():
        judge = SequenceJudge(decisions=[Decision("follow_up", "追问", "mock", 0.2)])
        flow = InterviewFlow(questions=QUESTIONS, judge=judge, global_turn_limit=1)

        await _bootstrap_to_wait(flow)
        resp = await flow.receive_candidate_answer("第一轮回答")
        assert resp.decision is not None
        assert resp.decision.reason == "global_turn_limit_reached"
        assert flow.state == WRAP_UP

    asyncio.run(_run())


def test_intro_mentions_dynamic_scenario_count():
    async def _run():
        judge = SequenceJudge(decisions=[Decision("next_question", "", "ok", 0.9)])
        one_question = [
            {
                "question_id": "q1",
                "main_question": "请做一个简短自我介绍。",
                "max_followups": 0,
                "max_clarifies": 0,
            }
        ]
        flow = InterviewFlow(questions=one_question, judge=judge)

        intro = await flow.produce_interviewer_message()
        assert "本场共1个场景" in intro.interviewer_text

    asyncio.run(_run())


def test_missing_max_clarifies_defaults_to_one():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("clarify", "请补充你负责的关键动作。", "clarify", 0.2),
                Decision("clarify", "继续澄清", "clarify", 0.2),
            ]
        )
        questions = [
            {
                "question_id": "q1",
                "main_question": "介绍项目经历。",
                "max_followups": 0,
            }
        ]
        flow = InterviewFlow(questions=questions, judge=judge)

        await _bootstrap_to_wait(flow)
        first = await flow.receive_candidate_answer("回答比较模糊")
        assert first.state_after == ASK_CLARIFY

        await _bootstrap_to_wait(flow)
        second = await flow.receive_candidate_answer("再次回答")
        assert second.decision is not None
        assert second.decision.reason == "clarify_limit_reached"
        assert second.state_after == WRAP_UP

    asyncio.run(_run())


def test_intro_counts_consecutive_same_scene_as_one_scenario():
    async def _run():
        judge = SequenceJudge(decisions=[Decision("next_question", "", "ok", 0.9)])
        questions = [
            {
                "question_id": "q1",
                "main_question": "介绍项目背景",
                "scenario": "项目复盘",
                "max_followups": 0,
                "max_clarifies": 0,
            },
            {
                "question_id": "q2",
                "main_question": "介绍你在项目中的职责",
                "scenario": "项目复盘",
                "max_followups": 0,
                "max_clarifies": 0,
            },
        ]
        flow = InterviewFlow(questions=questions, judge=judge)

        intro = await flow.produce_interviewer_message()
        assert "本场共1个场景" in intro.interviewer_text

    asyncio.run(_run())


def test_intro_counts_empty_scene_per_question():
    async def _run():
        judge = SequenceJudge(decisions=[Decision("next_question", "", "ok", 0.9)])
        questions = [
            {
                "question_id": "q1",
                "main_question": "问题1",
                "max_followups": 0,
                "max_clarifies": 0,
            },
            {
                "question_id": "q2",
                "main_question": "问题2",
                "max_followups": 0,
                "max_clarifies": 0,
            },
        ]
        flow = InterviewFlow(questions=questions, judge=judge)

        intro = await flow.produce_interviewer_message()
        assert "本场共2个场景" in intro.interviewer_text

    asyncio.run(_run())


def test_intro_counts_non_consecutive_same_scene_as_new_segment():
    async def _run():
        judge = SequenceJudge(decisions=[Decision("next_question", "", "ok", 0.9)])
        questions = [
            {
                "question_id": "q1",
                "main_question": "项目问题A",
                "scenario": "项目复盘",
                "max_followups": 0,
                "max_clarifies": 0,
            },
            {
                "question_id": "q2",
                "main_question": "故障问题B",
                "scenario": "故障处理",
                "max_followups": 0,
                "max_clarifies": 0,
            },
            {
                "question_id": "q3",
                "main_question": "项目问题C",
                "scenario": "项目复盘",
                "max_followups": 0,
                "max_clarifies": 0,
            },
        ]
        flow = InterviewFlow(questions=questions, judge=judge)

        intro = await flow.produce_interviewer_message()
        assert "本场共3个场景" in intro.interviewer_text

    asyncio.run(_run())


def test_export_question_answer_snapshots_aggregates_answers_per_question():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("follow_up", "继续补充结果", "need_more", 0.3),
                Decision("next_question", "", "enough", 0.8),
                Decision("next_question", "", "ok", 0.9),
            ]
        )
        questions = [
            {
                "question_id": "q1",
                "main_question": "介绍项目",
                "max_followups": 2,
                "max_clarifies": 2,
            },
            {
                "question_id": "q2",
                "main_question": "介绍协作",
                "max_followups": 0,
                "max_clarifies": 0,
            },
        ]
        flow = InterviewFlow(questions=questions, judge=judge)

        await _bootstrap_to_wait(flow)
        await flow.receive_candidate_answer("回答1")
        await _bootstrap_to_wait(flow)
        await flow.receive_candidate_answer("回答2")
        await _bootstrap_to_wait(flow)
        await flow.receive_candidate_answer("回答3")

        snapshots = flow.export_question_answer_snapshots()
        assert len(snapshots) == 2
        assert snapshots[0].candidate_answers == ["回答1", "回答2"]
        assert snapshots[0].aggregated_answer == "回答1\n回答2"
        assert snapshots[1].candidate_answers == ["回答3"]
        assert snapshots[1].aggregated_answer == "回答3"

    asyncio.run(_run())


def test_runtime_checkpoint_restore_and_rewind_to_current_question_start():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision("next_question", "", "ok", 0.9),
                Decision("next_question", "", "ok", 0.9),
            ]
        )
        flow = InterviewFlow(questions=QUESTIONS[:2], judge=judge)

        await _bootstrap_to_wait(flow)
        await flow.receive_candidate_answer("第一题回答")
        assert flow.current_question_index == 1
        assert flow.state == ASK_QUESTION

        checkpoint = flow.export_runtime_checkpoint()
        resumed = InterviewFlow(questions=QUESTIONS[:2], judge=SequenceJudge(decisions=[]))
        assert resumed.restore_runtime_checkpoint(checkpoint) is True
        assert resumed.current_question_index == 1
        assert resumed.questions[0].status == "done"
        assert resumed.questions[0].turns[0]["content"] == "第一题回答"
        assert resumed.rewind_to_question_start(resumed.current_question_index) is True
        assert resumed.state == ASK_QUESTION
        assert resumed.questions[1].status == "pending"
        assert resumed.questions[1].turns == []
        assert resumed.total_candidate_turns == 1

    asyncio.run(_run())
