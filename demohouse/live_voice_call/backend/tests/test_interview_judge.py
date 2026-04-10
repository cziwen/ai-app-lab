import asyncio

from interview_judge import InterviewJudge


async def _always_followup(*args, **kwargs):
    return (
        '{"next_action": "follow_up", "next_prompt": "请补充细节", '
        '"reason": "mock", "coverage_score": 0.3}'
    )


async def _broken_json(*args, **kwargs):
    return "not a json"


async def _conflicting_json(*args, **kwargs):
    return (
        '{"next_action": "invalid", "next_prompt": "继续追问", '
        '"reason": "conflict", "coverage_score": 0.9}'
    )


def test_empty_answer_prefers_followup():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        decision = await judge.decide(
            question="介绍一个项目",
            candidate_answer="嗯",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action == "follow_up"
        assert decision.reason == "answer_too_short"

    asyncio.run(_run())


def test_llm_parse_failure_fallback():
    async def _run():
        judge = InterviewJudge(llm_decider=_broken_json)
        decision = await judge.decide(
            question="介绍一个项目",
            candidate_answer="我负责过需求分析和上线",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action == "follow_up"
        assert decision.reason == "llm_parse_failure"

    asyncio.run(_run())


def test_decide_output_contract():
    async def _run():
        judge = InterviewJudge(llm_decider=_conflicting_json, coverage_threshold=0.7)
        decision = await judge.decide(
            question="介绍一个项目",
            candidate_answer="我负责需求分析、开发和上线复盘",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action in {"next_question", "follow_up", "clarify"}
        assert isinstance(decision.next_prompt, str)
        assert isinstance(decision.reason, str)
        assert 0.0 <= decision.coverage_score <= 1.0
        assert decision.next_action == "follow_up"
        assert decision.next_prompt == "继续追问"

    asyncio.run(_run())


def test_semantic_heuristic_uses_question_and_scoring_boundary():
    async def _run():
        judge = InterviewJudge(llm_endpoint_id=None, coverage_threshold=0.2)
        decision = await judge.decide(
            question="你如何处理客户投诉？",
            candidate_answer=(
                "我会先厘清事实再给可执行方案，"
                "然后确认客户是否认可，并跟进执行结果。"
            ),
            follow_up_count=0,
            clarify_count=0,
            evidence={"scoring_boundary": "是否先厘清事实再给可执行方案"},
        )
        assert decision.next_action == "next_question"
        assert decision.coverage_score >= 0.2

    asyncio.run(_run())


def test_semantic_heuristic_does_not_use_reference_or_best_standard():
    async def _run():
        judge = InterviewJudge(llm_endpoint_id=None, coverage_threshold=0.7)
        decision = await judge.decide(
            question="你如何处理客户投诉？",
            candidate_answer="我主要强调高净值、私域和成交转化。",
            follow_up_count=0,
            clarify_count=0,
            evidence={
                "scoring_boundary": "是否先厘清事实再给可执行方案",
                "reference_answer": "高净值、私域、成交转化",
                "best_standard": "高净值、私域、成交转化",
            },
        )
        assert decision.next_action == "follow_up"
        assert decision.reason == "semantic_need_more_detail"

    asyncio.run(_run())


def test_responses_adapter_path_uses_endpoint_and_thinking():
    class _FakeAdapter:
        def __init__(self):
            self.calls = []

        async def complete_text(self, **kwargs):
            self.calls.append(kwargs)
            return (
                '{"next_action": "next_question", "next_prompt": "", '
                '"reason": "ok", "coverage_score": 0.9}'
            )

    async def _run():
        adapter = _FakeAdapter()
        judge = InterviewJudge(
            llm_endpoint_id="ep-judge",
            llm_thinking_type="enabled",
            llm_reasoning_effort="minimal",
            responses_adapter=adapter,
        )
        decision = await judge.decide(
            question="介绍项目",
            candidate_answer="我负责拆解目标、推进开发并复盘结果。",
            follow_up_count=0,
            clarify_count=0,
            evidence={"scoring_boundary": "目标、动作、结果"},
        )
        assert decision.next_action == "next_question"
        assert adapter.calls
        call = adapter.calls[0]
        assert call["model"] == "ep-judge"
        assert call["thinking_type"] == "enabled"
        assert call["reasoning_effort"] == "minimal"

    asyncio.run(_run())


def test_clarify_request_repeat_prefers_question_replay():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        question = "介绍一个你做过的项目，并说明目标、动作和结果。"
        decision = await judge.decide(
            question=question,
            candidate_answer="嗯，我没太听懂这个题目，能再说一遍吗？",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action == "clarify"
        assert decision.reason == "candidate_requested_repeat"
        assert decision.next_prompt == question

    asyncio.run(_run())


def test_clarify_request_meaning_prefers_meaning_clarification():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        question = "请讲一个你推进跨团队协作并最终达成结果的例子。"
        decision = await judge.decide(
            question=question,
            candidate_answer="我不太理解这个题目什么意思，可以解释一下吗？",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action == "clarify"
        assert decision.reason == "candidate_requested_clarification"
        assert question in decision.next_prompt
        assert "请按这个题意继续作答。" in decision.next_prompt
        assert "？" not in decision.next_prompt

    asyncio.run(_run())


def test_clarify_request_synonyms_are_classified_consistently():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        question = "你如何处理复杂问题？"

        repeat = await judge.decide(
            question=question,
            candidate_answer="请你重说一遍这个题目，我没听清。",
            follow_up_count=0,
            clarify_count=0,
        )
        meaning = await judge.decide(
            question=question,
            candidate_answer="这个问题我没太理解，能再解释一下吗？",
            follow_up_count=0,
            clarify_count=0,
        )

        assert repeat.reason == "candidate_requested_repeat"
        assert meaning.reason == "candidate_requested_clarification"

    asyncio.run(_run())


def test_insufficient_answer_words_prefer_follow_up():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        decision = await judge.decide(
            question="你如何评估项目成效？",
            candidate_answer="不知道，不清楚。",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action == "follow_up"
        assert decision.reason == "candidate_answer_insufficient"

    asyncio.run(_run())


def test_mixed_insufficient_and_clarify_signals_prefer_clarify():
    async def _run():
        judge = InterviewJudge(llm_decider=_always_followup)
        decision = await judge.decide(
            question="请说明你做决策时的判断标准。",
            candidate_answer="我不清楚这题什么意思，请解释一下。",
            follow_up_count=0,
            clarify_count=0,
        )
        assert decision.next_action == "clarify"
        assert decision.reason == "candidate_requested_clarification"

    asyncio.run(_run())
