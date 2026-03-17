import asyncio
from dataclasses import dataclass
from typing import List

from interview_flow import (
    ASK_FOLLOWUP,
    ASK_QUESTION,
    DONE,
    WAIT_ANSWER,
    WRAP_UP,
    InterviewFlow,
)
from interview_judge import Decision, InterviewJudge


QUESTIONS = [
    {"question_id": "q1", "main_question": "请做一个简短自我介绍。"},
    {"question_id": "q2", "main_question": "介绍一个你主导的项目。"},
    {"question_id": "q3", "main_question": "讲讲你如何处理复杂问题。"},
]


@dataclass
class SequenceJudge:
    decisions: List[Decision]

    async def decide(self, question, candidate_answer, follow_up_count, evidence=None):
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
    assert flow.state == WAIT_ANSWER


def test_happy_path_all_move_forward():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision(True, False, "", "ok", 0.9),
                Decision(True, False, "", "ok", 0.9),
                Decision(True, False, "", "ok", 0.9),
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
                Decision(False, True, "请补充你的职责", "need_more", 0.3),
                Decision(True, False, "", "enough", 0.8),
                Decision(True, False, "", "ok", 0.8),
                Decision(True, False, "", "ok", 0.8),
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


def test_max_followups_forces_move_forward():
    async def _run():
        async def _always_followup(*args, **kwargs):
            return (
                '{"move_forward": false, "need_follow_up": true, '
                '"follow_up_question": "再具体一点", "reason": "mock", "coverage_score": 0.1}'
            )

        judge = InterviewJudge(
            llm_decider=_always_followup,
            max_followups_per_question=2,
        )
        flow = InterviewFlow(
            questions=QUESTIONS,
            judge=judge,
            max_followups_per_question=2,
        )

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


def test_last_question_wraps_up():
    async def _run():
        judge = SequenceJudge(
            decisions=[
                Decision(True, False, "", "ok", 0.9),
                Decision(True, False, "", "ok", 0.9),
                Decision(True, False, "", "ok", 0.9),
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
        judge = SequenceJudge(
            decisions=[Decision(False, True, "追问", "mock", 0.2)]
        )
        flow = InterviewFlow(questions=QUESTIONS, judge=judge, global_turn_limit=1)

        await _bootstrap_to_wait(flow)
        resp = await flow.receive_candidate_answer("第一轮回答")
        assert resp.decision is not None
        assert resp.decision.reason == "global_turn_limit_reached"
        assert flow.state == WRAP_UP

    asyncio.run(_run())
