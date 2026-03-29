import asyncio

from interview_scorer import InterviewScorer


def test_empty_answer_returns_zero_without_llm_call():
    async def _run():
        async def _should_not_call(_payload):
            assert False, "llm_decider should not be called for empty answer"

        scorer = InterviewScorer(llm_decider=_should_not_call)
        result = await scorer.score_question(
            {
                "question_id": "q1",
                "sort_order": 1,
                "question": "请介绍一个项目",
                "aggregated_answer": "   ",
            }
        )
        assert result.numeric_score == 0.0
        assert "未给出有效回答" in result.comment

    asyncio.run(_run())


def test_parse_and_clamp_score_from_llm_json():
    async def _run():
        async def _mock_decider(_payload):
            return 'prefix {"numeric_score": 6.7, "comment": "覆盖较完整"} suffix'

        scorer = InterviewScorer(llm_decider=_mock_decider, raw_preview_chars=1000)
        result = await scorer.score_question(
            {
                "question_id": "q2",
                "sort_order": 2,
                "question": "你如何推进跨团队协作",
                "aggregated_answer": "我会先对齐目标，然后明确里程碑并复盘。",
            }
        )
        assert result.numeric_score == 5.0
        assert result.comment == "覆盖较完整"
        assert result.debug_meta["parse_fallback_used"] is True
        assert result.debug_meta["numeric_score_was_clamped"] is True

    asyncio.run(_run())


def test_score_interview_computes_average_with_rounding():
    async def _run():
        replies = iter(
            [
                '{"numeric_score": 3.0, "comment": "中等"}',
                '{"numeric_score": 4.333, "comment": "较好"}',
            ]
        )

        async def _mock_decider(_payload):
            return next(replies)

        scorer = InterviewScorer(llm_decider=_mock_decider)
        scorecard = await scorer.score_interview(
            [
                {
                    "question_id": "q1",
                    "sort_order": 1,
                    "question": "Q1",
                    "aggregated_answer": "A1",
                },
                {
                    "question_id": "q2",
                    "sort_order": 2,
                    "question": "Q2",
                    "aggregated_answer": "A2",
                },
            ]
        )
        assert scorecard.overall_score == 3.67
        assert len(scorecard.question_scores) == 2

    asyncio.run(_run())


def test_missing_llm3_endpoint_raises_for_non_empty_answer():
    async def _run():
        scorer = InterviewScorer(llm_endpoint_id=None)
        try:
            await scorer.score_question(
                {
                    "question_id": "q1",
                    "sort_order": 1,
                    "question": "Q1",
                    "aggregated_answer": "候选人回答",
                }
            )
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "LLM3_ENDPOINT_ID missing" in str(exc)

    asyncio.run(_run())


def test_raw_output_preview_is_truncated_and_error_meta_attached():
    async def _run():
        async def _mock_decider(_payload):
            return "x" * 220

        scorer = InterviewScorer(llm_decider=_mock_decider, raw_preview_chars=100)
        try:
            await scorer.score_question(
                {
                    "question_id": "q9",
                    "sort_order": 9,
                    "question": "Q9",
                    "aggregated_answer": "候选人回答",
                }
            )
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "LLM3 parse failure" in str(exc)
            debug_meta = getattr(exc, "scoring_debug_meta", {})
            assert debug_meta.get("stage") == "parse"
            assert debug_meta.get("raw_output_truncated") is True
            assert len(debug_meta.get("raw_output_preview", "")) <= 100

    asyncio.run(_run())
