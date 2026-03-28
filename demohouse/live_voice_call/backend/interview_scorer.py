import json
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ark_responses_adapter import ArkResponsesAdapter
from llm_limiter import llm_slot

SCORER_INSTRUCTIONS = (
    "你是结构化面试评分器。只输出一个JSON对象，不要输出任何额外文字。"
    "JSON字段必须包含：numeric_score(float 0~5), comment(str)。"
    "评分时请严格参考提供的 rubric 字段与候选人聚合回答。"
)


@dataclass
class QuestionScoreResult:
    question_id: str
    sort_order: int
    question: str
    ability_dimension: str
    output_format: str
    aggregated_answer: str
    numeric_score: float
    comment: str


@dataclass
class ScorecardResult:
    overall_score: float
    question_scores: List[QuestionScoreResult]


class InterviewScorer:
    def __init__(
        self,
        llm_endpoint_id: Optional[str] = None,
        llm_thinking_type: str = "disabled",
        llm_reasoning_effort: Optional[str] = None,
        responses_adapter: Optional[ArkResponsesAdapter] = None,
        llm_decider: Optional[Callable[[Dict[str, Any]], Awaitable[str]]] = None,
    ) -> None:
        self.llm_endpoint_id = llm_endpoint_id
        self.llm_thinking_type = llm_thinking_type
        self.llm_reasoning_effort = llm_reasoning_effort
        self.responses_adapter = responses_adapter
        self.llm_decider = llm_decider

    async def score_interview(self, questions: List[Dict[str, Any]]) -> ScorecardResult:
        results: List[QuestionScoreResult] = []
        for payload in questions:
            results.append(await self.score_question(payload))

        if not results:
            return ScorecardResult(overall_score=0.0, question_scores=[])

        total = sum(item.numeric_score for item in results)
        overall = round(total / len(results), 2)
        return ScorecardResult(overall_score=overall, question_scores=results)

    async def score_question(self, payload: Dict[str, Any]) -> QuestionScoreResult:
        question_id = str(payload.get("question_id", "") or "")
        sort_order = int(payload.get("sort_order", 0) or 0)
        question = str(payload.get("question", "") or "").strip()
        ability_dimension = str(payload.get("ability_dimension", "") or "").strip()
        output_format = str(payload.get("output_format", "") or "").strip()
        aggregated_answer = str(payload.get("aggregated_answer", "") or "").strip()

        if not aggregated_answer:
            return QuestionScoreResult(
                question_id=question_id,
                sort_order=sort_order,
                question=question,
                ability_dimension=ability_dimension,
                output_format=output_format,
                aggregated_answer="",
                numeric_score=0.0,
                comment="候选人未给出有效回答。",
            )

        raw = await self._call_llm(payload)
        parsed = self._parse_score_json(raw)
        numeric_score = parsed["numeric_score"]
        comment = parsed["comment"]

        return QuestionScoreResult(
            question_id=question_id,
            sort_order=sort_order,
            question=question,
            ability_dimension=ability_dimension,
            output_format=output_format,
            aggregated_answer=aggregated_answer,
            numeric_score=numeric_score,
            comment=comment,
        )

    async def _call_llm(self, payload: Dict[str, Any]) -> str:
        if self.llm_decider is not None:
            return await self.llm_decider(payload)

        if not self.llm_endpoint_id:
            raise RuntimeError("LLM3_ENDPOINT_ID missing")

        adapter = self.responses_adapter
        if adapter is None:
            api_key = (os.getenv("ARK_API_KEY") or "").strip()
            if not api_key:
                raise RuntimeError("ARK_API_KEY missing")
            adapter = ArkResponsesAdapter(api_key=api_key)

        llm_payload = {
            "question": str(payload.get("question", "") or "").strip(),
            "ability_dimension": str(payload.get("ability_dimension", "") or "").strip(),
            "scoring_boundary": str(payload.get("scoring_boundary", "") or "").strip(),
            "best_standard": str(payload.get("best_standard", "") or "").strip(),
            "medium_standard": str(payload.get("medium_standard", "") or "").strip(),
            "worst_standard": str(payload.get("worst_standard", "") or "").strip(),
            "output_format": str(payload.get("output_format", "") or "").strip(),
            "candidate_answer": str(payload.get("aggregated_answer", "") or "").strip(),
            "instruction": (
                "请输出 numeric_score(0~5) 与 comment。"
                "comment 需说明候选人与 rubric 的匹配度，并给出简短改进建议。"
            ),
        }

        async with llm_slot():
            result = await adapter.complete_text(
                model=self.llm_endpoint_id,
                instructions=SCORER_INSTRUCTIONS,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(llm_payload, ensure_ascii=False),
                    }
                ],
                thinking_type=self.llm_thinking_type,
                reasoning_effort=self.llm_reasoning_effort,
            )
        if not result:
            raise RuntimeError("LLM3 returned empty content")
        return result

    def _parse_score_json(self, raw: str) -> Dict[str, Any]:
        content = (raw or "").strip()
        parsed: Optional[Dict[str, Any]] = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    parsed = None

        if not isinstance(parsed, dict):
            raise RuntimeError("LLM3 parse failure")

        try:
            numeric_score = float(parsed.get("numeric_score", 0.0))
        except (TypeError, ValueError):
            numeric_score = 0.0
        numeric_score = max(0.0, min(5.0, numeric_score))

        comment = str(parsed.get("comment", "") or "").strip()
        if not comment:
            comment = "模型未返回有效评语。"

        return {
            "numeric_score": numeric_score,
            "comment": comment,
        }
