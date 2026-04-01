import json
import os
from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ark_responses_adapter import ArkResponsesAdapter
from llm_limiter import llm_slot
from score_scale import parse_score_scale

SCORER_INSTRUCTIONS = (
    "你是结构化面试评分器。只输出一个JSON对象，不要输出任何额外文字。"
    "JSON字段必须包含：numeric_score(float), comment(str)。"
    "请优先参考提供的 rubric 字段，并结合候选人回答中的有效证据做综合判断。"
    "当回答与 rubric 不完全贴合时，可给中间分，不要默认极低分。"
)


@dataclass
class QuestionScoreResult:
    question_id: str
    sort_order: int
    question: str
    ability_dimension: str
    score_format: str
    comment_requirement: str
    max_score: float
    score_error: str
    aggregated_answer: str
    numeric_score: float
    comment: str
    debug_meta: Dict[str, Any]


@dataclass
class ScorecardResult:
    overall_score: Optional[float]
    total_score: float
    total_max_score: float
    question_scores: List[QuestionScoreResult]


class InterviewScorer:
    def __init__(
        self,
        llm_endpoint_id: Optional[str] = None,
        llm_thinking_type: str = "disabled",
        llm_reasoning_effort: Optional[str] = None,
        responses_adapter: Optional[ArkResponsesAdapter] = None,
        llm_decider: Optional[Callable[[Dict[str, Any]], Awaitable[str]]] = None,
        raw_preview_chars: Optional[int] = None,
    ) -> None:
        self.llm_endpoint_id = llm_endpoint_id
        self.llm_thinking_type = llm_thinking_type
        self.llm_reasoning_effort = llm_reasoning_effort
        self.responses_adapter = responses_adapter
        self.llm_decider = llm_decider
        preview_chars = raw_preview_chars
        if preview_chars is None:
            try:
                preview_chars = int(os.getenv("SCORING_LOG_RAW_PREVIEW_CHARS", "800"))
            except ValueError:
                preview_chars = 800
        self.raw_preview_chars = max(100, int(preview_chars))

    async def score_interview(self, questions: List[Dict[str, Any]]) -> ScorecardResult:
        results: List[QuestionScoreResult] = []
        for payload in questions:
            results.append(await self.score_question(payload))

        if not results:
            return ScorecardResult(
                overall_score=None,
                total_score=0.0,
                total_max_score=0.0,
                question_scores=[],
            )

        total_score = round(sum(item.numeric_score for item in results), 2)
        total_max_score = round(sum(item.max_score for item in results), 2)
        return ScorecardResult(
            overall_score=None,
            total_score=total_score,
            total_max_score=total_max_score,
            question_scores=results,
        )

    async def score_question(self, payload: Dict[str, Any]) -> QuestionScoreResult:
        started = monotonic()
        question_id = str(payload.get("question_id", "") or "")
        sort_order = int(payload.get("sort_order", 0) or 0)
        question = str(payload.get("question", "") or "").strip()
        ability_dimension = str(payload.get("ability_dimension", "") or "").strip()
        score_format = str(payload.get("score_format", "") or "").strip()
        comment_requirement = str(payload.get("comment_requirement", "") or "").strip()
        aggregated_answer = str(payload.get("aggregated_answer", "") or "").strip()
        max_score, scale_error = parse_score_scale(score_format)
        if max_score is None:
            return QuestionScoreResult(
                question_id=question_id,
                sort_order=sort_order,
                question=question,
                ability_dimension=ability_dimension,
                score_format=score_format,
                comment_requirement=comment_requirement,
                max_score=0.0,
                score_error=f"分数配置无效：{score_format or '(空)'}；{scale_error or ''}".strip(),
                aggregated_answer=aggregated_answer,
                numeric_score=0.0,
                comment="该题分数配置无效，系统按0分处理。",
                debug_meta={
                    "elapsed_ms": int((monotonic() - started) * 1000),
                    "skipped_empty_answer": False,
                    "raw_output_preview": "",
                    "raw_output_truncated": False,
                    "parse_fallback_used": False,
                    "numeric_score_was_clamped": False,
                },
            )

        if not aggregated_answer:
            return QuestionScoreResult(
                question_id=question_id,
                sort_order=sort_order,
                question=question,
                ability_dimension=ability_dimension,
                score_format=score_format,
                comment_requirement=comment_requirement,
                max_score=max_score,
                score_error="",
                aggregated_answer="",
                numeric_score=0.0,
                comment="候选人未给出有效回答。",
                debug_meta={
                    "elapsed_ms": int((monotonic() - started) * 1000),
                    "skipped_empty_answer": True,
                    "raw_output_preview": "",
                    "raw_output_truncated": False,
                    "parse_fallback_used": False,
                    "numeric_score_was_clamped": False,
                },
            )

        try:
            raw = await self._call_llm(payload)
        except Exception as llm_err:
            setattr(
                llm_err,
                "scoring_debug_meta",
                {
                    "stage": "llm_call",
                    "question_id": question_id,
                    "sort_order": sort_order,
                    "elapsed_ms": int((monotonic() - started) * 1000),
                },
            )
            raise

        try:
            parsed = self._parse_score_json(raw, max_score=max_score)
        except Exception as parse_err:
            raw_preview, raw_truncated = self._build_raw_preview(raw)
            setattr(
                parse_err,
                "scoring_debug_meta",
                {
                    "stage": "parse",
                    "question_id": question_id,
                    "sort_order": sort_order,
                    "elapsed_ms": int((monotonic() - started) * 1000),
                    "raw_output_preview": raw_preview,
                    "raw_output_truncated": raw_truncated,
                },
            )
            raise

        numeric_score = parsed["numeric_score"]
        comment = parsed["comment"]
        raw_preview, raw_truncated = self._build_raw_preview(raw)

        return QuestionScoreResult(
            question_id=question_id,
            sort_order=sort_order,
            question=question,
            ability_dimension=ability_dimension,
            score_format=score_format,
            comment_requirement=comment_requirement,
            max_score=max_score,
            score_error="",
            aggregated_answer=aggregated_answer,
            numeric_score=numeric_score,
            comment=comment,
            debug_meta={
                "elapsed_ms": int((monotonic() - started) * 1000),
                "skipped_empty_answer": False,
                "raw_output_preview": raw_preview,
                "raw_output_truncated": raw_truncated,
                "parse_fallback_used": bool(parsed["parse_fallback_used"]),
                "numeric_score_was_clamped": bool(parsed["numeric_score_was_clamped"]),
            },
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
            "score_format": str(payload.get("score_format", "") or "").strip(),
            "comment_requirement": str(
                payload.get("comment_requirement", "") or ""
            ).strip(),
            "candidate_answer": str(payload.get("aggregated_answer", "") or "").strip(),
            "instruction": (
                f"请输出 numeric_score(0~{max_score:g}) 与 comment。"
                "评分原则：rubric优先、证据加权、避免一票否决。"
                "若信息不足，给保守中间分并指出缺失信息；仅在明显不匹配时给低分。"
                "comment 需简要说明命中点、缺失点，并给出改进建议。"
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

    def _parse_score_json(self, raw: str, *, max_score: float) -> Dict[str, Any]:
        content = (raw or "").strip()
        parsed: Optional[Dict[str, Any]] = None
        parse_fallback_used = False
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(content[start : end + 1])
                    parse_fallback_used = True
                except json.JSONDecodeError:
                    parsed = None

        if not isinstance(parsed, dict):
            raise RuntimeError("LLM3 parse failure")

        raw_numeric_score = parsed.get("numeric_score", 0.0)
        try:
            numeric_score = float(raw_numeric_score)
        except (TypeError, ValueError):
            numeric_score = 0.0
        clamped_score = max(0.0, min(float(max_score), numeric_score))
        numeric_score_was_clamped = clamped_score != numeric_score
        numeric_score = clamped_score

        comment = str(parsed.get("comment", "") or "").strip()
        if not comment:
            comment = "模型未返回有效评语。"

        return {
            "numeric_score": numeric_score,
            "comment": comment,
            "parse_fallback_used": parse_fallback_used,
            "numeric_score_was_clamped": numeric_score_was_clamped,
        }

    def _build_raw_preview(self, raw: str) -> Tuple[str, bool]:
        content = str(raw or "")
        if len(content) <= self.raw_preview_chars:
            return content, False
        return content[: self.raw_preview_chars], True
