import asyncio

from interview_judge import InterviewJudge


async def _always_followup(*args, **kwargs):
    return (
        '{"move_forward": false, "need_follow_up": true, '
        '"follow_up_question": "请补充细节", "reason": "mock", "coverage_score": 0.3}'
    )


async def _broken_json(*args, **kwargs):
    return "not a json"


async def _conflicting_json(*args, **kwargs):
    return (
        '{"move_forward": true, "need_follow_up": true, '
        '"follow_up_question": "继续追问", "reason": "conflict", "coverage_score": 0.9}'
    )


def test_guard_overrides_llm():
    async def _run():
        judge = InterviewJudge(
            llm_decider=_always_followup,
            max_followups_per_question=2,
        )
        decision = await judge.decide(
            question="介绍一个项目", candidate_answer="我做过很多", follow_up_count=2
        )
        assert decision.move_forward is True
        assert decision.need_follow_up is False
        assert decision.reason == "follow_up_limit_reached"

    asyncio.run(_run())


def test_empty_answer_prefers_followup():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        decision = await judge.decide(
            question="介绍一个项目", candidate_answer="嗯", follow_up_count=0
        )
        assert decision.move_forward is False
        assert decision.need_follow_up is True
        assert decision.reason == "answer_too_short"

    asyncio.run(_run())


def test_llm_parse_failure_fallback():
    async def _run():
        judge = InterviewJudge(llm_decider=_broken_json)
        decision = await judge.decide(
            question="介绍一个项目",
            candidate_answer="我负责过需求分析和上线",
            follow_up_count=0,
        )
        assert decision.move_forward is False
        assert decision.need_follow_up is True
        assert decision.reason == "llm_parse_failure"

    asyncio.run(_run())


def test_decide_output_contract():
    async def _run():
        judge = InterviewJudge(llm_decider=_conflicting_json, coverage_threshold=0.7)
        decision = await judge.decide(
            question="介绍一个项目",
            candidate_answer="我负责需求分析、开发和上线复盘",
            follow_up_count=0,
        )
        assert isinstance(decision.move_forward, bool)
        assert isinstance(decision.need_follow_up, bool)
        assert isinstance(decision.follow_up_question, str)
        assert isinstance(decision.reason, str)
        assert 0.0 <= decision.coverage_score <= 1.0
        assert decision.move_forward is True
        assert decision.need_follow_up is False
        assert decision.follow_up_question == ""

    asyncio.run(_run())


def test_semantic_heuristic_uses_question_and_scoring_boundary():
    async def _run():
        judge = InterviewJudge(llm_endpoint_id=None, coverage_threshold=0.7)
        decision = await judge.decide(
            question="你如何处理客户投诉？",
            candidate_answer="我会先厘清事实，再给出可执行方案，并确认客户是否认可。",
            follow_up_count=0,
            evidence={"scoring_boundary": "是否先厘清事实再给可执行方案"},
        )
        assert decision.move_forward is True
        assert decision.need_follow_up is False
        assert decision.coverage_score >= 0.7

    asyncio.run(_run())


def test_semantic_heuristic_does_not_use_reference_or_best_standard():
    async def _run():
        judge = InterviewJudge(llm_endpoint_id=None, coverage_threshold=0.7)
        decision = await judge.decide(
            question="你如何处理客户投诉？",
            candidate_answer="我主要强调高净值、私域和成交转化。",
            follow_up_count=0,
            evidence={
                "scoring_boundary": "是否先厘清事实再给可执行方案",
                "reference_answer": "高净值、私域、成交转化",
                "best_standard": "高净值、私域、成交转化",
            },
        )
        assert decision.move_forward is False
        assert decision.need_follow_up is True
        assert decision.reason == "semantic_need_more_detail"

    asyncio.run(_run())
