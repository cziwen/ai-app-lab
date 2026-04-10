import json
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ark_responses_adapter import ArkResponsesAdapter
from llm_limiter import llm_slot


@dataclass
class Decision:
    next_action: str
    next_prompt: str
    reason: str
    coverage_score: float

    @property
    def move_forward(self) -> bool:
        return self.next_action == "next_question"

    @property
    def need_follow_up(self) -> bool:
        return self.next_action == "follow_up"

    @property
    def follow_up_question(self) -> str:
        if self.next_action != "follow_up":
            return ""
        return self.next_prompt


JUDGE_INSTRUCTIONS = (
    "你是结构化面试裁决器。只输出一个JSON对象，不要输出任何额外文字。"
    "JSON字段必须包含：next_action(str), next_prompt(str), reason(str), coverage_score(float 0~1)。"
    "next_action 只能是 next_question/follow_up/clarify 三选一。"
    "遵守：当 next_action=next_question 时，next_prompt 必须为空字符串；"
    "当 next_action=follow_up 时，next_prompt 必须是追问句；"
    "当 next_action=clarify 时，next_prompt 必须是题意澄清，不能新增考察点。"
    "当 next_action=clarify 时，next_prompt 必须是解释性陈述，禁止反问、禁止二选一提问。"
    "clarify 仅用于候选人明确表示听不懂、没理解题目、请求解释。"
    "follow_up 仅用于候选人已经作答但信息不足，需要继续深挖。"
    "候选人回答“不知道/不清楚/不会/没想好”时，一律视为回答不足，返回 follow_up。"
    "仅当候选人明确表示“没听清/没听懂/请重复题目/请解释题意/什么意思”时，才允许返回 clarify。"
    "若回答同时包含“回答不足词”和“求澄清词”，优先返回 clarify。"
    "评分时只允许参考：题目(question)、评分分界线(scoring_boundary)、候选人回答(candidate_answer)。"
    "禁止参考 must_cover、reference_answer、best_standard、medium_standard、worst_standard。"
)


class InterviewJudge:
    def __init__(
        self,
        coverage_threshold: float = 0.7,
        llm_endpoint_id: Optional[str] = None,
        llm_thinking_type: str = "disabled",
        llm_reasoning_effort: Optional[str] = None,
        responses_adapter: Optional[ArkResponsesAdapter] = None,
        llm_decider: Optional[
            Callable[[str, str, int, int, Optional[Dict[str, Any]]], Awaitable[str]]
        ] = None,
    ):
        self.coverage_threshold = coverage_threshold
        self.llm_endpoint_id = llm_endpoint_id
        self.llm_thinking_type = llm_thinking_type
        self.llm_reasoning_effort = llm_reasoning_effort
        self.responses_adapter = responses_adapter
        self.llm_decider = llm_decider

    async def decide(
        self,
        question: str,
        candidate_answer: str,
        follow_up_count: int,
        clarify_count: int,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Decision:
        answer = (candidate_answer or "").strip()

        clarify_subtype = self._classify_clarify_request(answer)
        insufficient_answer = self._is_insufficient_answer(answer)
        # Priority: explicit clarification request overrides insufficiency wording.
        if clarify_subtype == "repeat_request":
            repeat_prompt = (question or "").strip() or (
                "我复述一下题目。请按题意继续作答。"
            )
            return Decision(
                next_action="clarify",
                next_prompt=repeat_prompt,
                reason="candidate_requested_repeat",
                coverage_score=0.0,
            )
        if clarify_subtype == "meaning_clarify":
            return Decision(
                next_action="clarify",
                next_prompt=self._build_meaning_clarify_prompt(question),
                reason="candidate_requested_clarification",
                coverage_score=0.0,
            )
        if insufficient_answer:
            return Decision(
                next_action="follow_up",
                next_prompt="我先听你讲一个最接近的真实案例，再补充你的行动和结果。",
                reason="candidate_answer_insufficient",
                coverage_score=0.0,
            )

        # Guard: empty/too short answer -> prefer follow-up.
        if len(answer) < 6:
            return Decision(
                next_action="follow_up",
                next_prompt="请你结合一个具体经历，再展开说明一下。",
                reason="answer_too_short",
                coverage_score=0.0,
            )

        raw = await self._call_llm(question, answer, follow_up_count, clarify_count, evidence)
        if raw is None:
            return self._fallback_follow_up("llm_unavailable")

        parsed = self._parse_json(raw)
        if parsed is None:
            return self._fallback_follow_up("llm_parse_failure")

        return self._normalize(parsed)

    async def _call_llm(
        self,
        question: str,
        candidate_answer: str,
        follow_up_count: int,
        clarify_count: int,
        evidence: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if self.llm_decider is not None:
            try:
                return await self.llm_decider(
                    question, candidate_answer, follow_up_count, clarify_count, evidence
                )
            except TypeError:
                # Backward-compatible path for legacy custom deciders.
                return await self.llm_decider(
                    question, candidate_answer, follow_up_count, evidence
                )

        if not self.llm_endpoint_id:
            return self._heuristic_decision_json(question, candidate_answer, evidence)

        adapter = self.responses_adapter
        if adapter is None:
            api_key = (os.getenv("ARK_API_KEY") or "").strip()
            if not api_key:
                return None
            adapter = ArkResponsesAdapter(api_key=api_key)

        prompt_payload = {
            "question": question,
            "scoring_boundary": self._extract_scoring_boundary(evidence),
            "candidate_answer": candidate_answer,
            "follow_up_count": follow_up_count,
            "clarify_count": clarify_count,
            "instruction": (
                "仅基于 question 与 scoring_boundary 评估 candidate_answer。"
                "如果是求解释题意，返回 clarify；"
                "如果回答不足但已作答，返回 follow_up；"
                "如果已可进入下一题，返回 next_question。仅返回JSON。"
            ),
        }
        async with llm_slot():
            result = await adapter.complete_text(
                model=self.llm_endpoint_id,
                instructions=JUDGE_INSTRUCTIONS,
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(prompt_payload, ensure_ascii=False),
                    }
                ],
                thinking_type=self.llm_thinking_type,
                reasoning_effort=self.llm_reasoning_effort,
            )
        if not result:
            return None
        return result

    def _parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        content = raw.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1 or start >= end:
                return None
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return None

    def _normalize(self, payload: Dict[str, Any]) -> Decision:
        action = str(payload.get("next_action", "") or "").strip().lower()
        next_prompt = str(payload.get("next_prompt", "") or "").strip()
        reason = str(payload.get("reason", "llm_decision") or "llm_decision")

        try:
            coverage_score = float(payload.get("coverage_score", 0.0))
        except (TypeError, ValueError):
            coverage_score = 0.0
        coverage_score = max(0.0, min(1.0, coverage_score))

        # Backward compatibility: accept old-format payload from judge model.
        if not action:
            move_forward = bool(payload.get("move_forward", False))
            need_follow_up = bool(payload.get("need_follow_up", False))
            legacy_follow_up_question = str(payload.get("follow_up_question", "") or "").strip()
            if move_forward:
                action = "next_question"
            elif need_follow_up:
                action = "follow_up"
            else:
                action = "follow_up"
            if not next_prompt:
                next_prompt = legacy_follow_up_question

        if action not in {"next_question", "follow_up", "clarify"}:
            action = "follow_up"

        if action == "next_question":
            next_prompt = ""
        elif action == "follow_up" and not next_prompt:
            next_prompt = "请你补充一个更具体的案例和结果。"
        elif action == "clarify" and not next_prompt:
            next_prompt = "我的意思是：请围绕问题本身，结合你的真实经历来说明。"

        return Decision(
            next_action=action,
            next_prompt=next_prompt,
            reason=reason,
            coverage_score=coverage_score,
        )

    def _fallback_follow_up(self, reason: str) -> Decision:
        return Decision(
            next_action="follow_up",
            next_prompt="我想听到更具体的行动和结果，请再展开一下。",
            reason=reason,
            coverage_score=0.0,
        )

    def _heuristic_decision_json(
        self, question: str, candidate_answer: str, evidence: Optional[Dict[str, Any]]
    ) -> str:
        clarify_subtype = self._classify_clarify_request(candidate_answer)
        insufficient_answer = self._is_insufficient_answer(candidate_answer)
        if clarify_subtype == "repeat_request":
            repeat_prompt = (question or "").strip() or (
                "我复述一下题目。请按题意继续作答。"
            )
            decision = {
                "next_action": "clarify",
                "next_prompt": repeat_prompt,
                "reason": "candidate_requested_repeat",
                "coverage_score": 0.0,
            }
            return json.dumps(decision, ensure_ascii=False)
        if insufficient_answer:
            decision = {
                "next_action": "follow_up",
                "next_prompt": "我先听你讲一个最接近的真实案例，再补充你的行动和结果。",
                "reason": "candidate_answer_insufficient",
                "coverage_score": 0.0,
            }
            return json.dumps(decision, ensure_ascii=False)
        if clarify_subtype == "meaning_clarify":
            decision = {
                "next_action": "clarify",
                "next_prompt": self._build_meaning_clarify_prompt(question),
                "reason": "candidate_requested_clarification",
                "coverage_score": 0.0,
            }
            return json.dumps(decision, ensure_ascii=False)

        scoring_boundary = self._extract_scoring_boundary(evidence)
        rubric_keywords = self._extract_keywords(f"{question} {scoring_boundary}".strip())
        answer_keywords = self._extract_keywords(candidate_answer)

        if not answer_keywords:
            coverage = 0.0
        elif not rubric_keywords:
            coverage = min(1.0, max(0.2, len(candidate_answer.strip()) / 120))
        else:
            rubric_set = set(rubric_keywords)
            answer_set = set(answer_keywords)
            overlap_count = len(rubric_set & answer_set)
            coverage = overlap_count / len(rubric_set)

        if coverage < self.coverage_threshold and len(candidate_answer.strip()) >= 80:
            coverage = max(coverage, self.coverage_threshold - 0.05)

        if coverage >= self.coverage_threshold:
            decision = {
                "next_action": "next_question",
                "next_prompt": "",
                "reason": "semantic_enough_coverage",
                "coverage_score": coverage,
            }
        else:
            decision = {
                "next_action": "follow_up",
                "next_prompt": (
                    "请围绕这个评价标准补充："
                    f"{scoring_boundary or '你是如何判断并落实关键决策的'}。"
                ),
                "reason": "semantic_need_more_detail",
                "coverage_score": coverage,
            }

        return json.dumps(decision, ensure_ascii=False)

    def _extract_scoring_boundary(self, evidence: Optional[Dict[str, Any]]) -> str:
        if not isinstance(evidence, dict):
            return ""
        return str(evidence.get("scoring_boundary", "") or "").strip()

    def _extract_keywords(self, text: str) -> List[str]:
        normalized = (
            (text or "")
            .replace("，", " ")
            .replace("。", " ")
            .replace("；", " ")
            .replace("：", " ")
            .replace("、", " ")
            .replace(",", " ")
            .replace(";", " ")
            .replace(":", " ")
        )
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", normalized)
        if not tokens:
            return []
        picked: List[str] = []
        seen = set()
        for token in tokens:
            candidates = [token]
            if re.search(r"[\u4e00-\u9fff]", token) and len(token) >= 4:
                candidates.extend(token[idx : idx + 2] for idx in range(len(token) - 1))
            for candidate in candidates:
                if len(candidate) < 2 or candidate in seen:
                    continue
                seen.add(candidate)
                picked.append(candidate)
                if len(picked) >= 16:
                    return picked
            if len(picked) >= 16:
                break
        return picked

    def _normalize_clarify_text(self, answer: str) -> str:
        normalized = (answer or "").strip().lower()
        if not normalized:
            return ""
        replacements = {
            "？": "?",
            "，": ",",
            "。": ".",
            "！": "!",
            "：": ":",
            "；": ";",
            "　": " ",
            "重新再说一遍": "再说一遍",
            "重新再讲一遍": "再讲一遍",
            "重说一遍": "再说一遍",
            "重讲一遍": "再讲一遍",
            "再重复一遍": "重复一遍",
            "重新说一遍": "再说一遍",
            "重新讲一遍": "再讲一遍",
        }
        for src, target in replacements.items():
            normalized = normalized.replace(src, target)
        normalized = re.sub(r"\s+", "", normalized)
        return normalized

    def _classify_clarify_request(self, answer: str) -> Optional[str]:
        normalized = self._normalize_clarify_text(answer)
        if not normalized:
            return None

        repeat_signals = [
            "没听清",
            "听不清",
            "没听懂",
            "没太听懂",
            "再说一遍",
            "再讲一遍",
            "重复一遍",
            "题目是什么",
            "重新说",
            "重新讲",
            "再说一次",
            "重说",
            "重讲",
        ]
        meaning_signals = [
            "没理解",
            "没太理解",
            "不理解",
            "不太理解",
            "什么意思",
            "什么问题",
            "怎么理解",
            "请解释",
            "再解释",
            "解释一下",
            "我没懂",
            "我不太懂",
            "为什么说",
            "为什么是",
        ]

        for signal in repeat_signals:
            if signal in normalized:
                return "repeat_request"
        for signal in meaning_signals:
            if signal in normalized:
                return "meaning_clarify"
        return None

    def _is_insufficient_answer(self, answer: str) -> bool:
        normalized = self._normalize_clarify_text(answer)
        if not normalized:
            return False

        insufficient_signals = [
            "不知道",
            "不清楚",
            "不会",
            "没想好",
            "想不出来",
            "答不上来",
        ]
        return any(signal in normalized for signal in insufficient_signals)

    def _is_clarify_request(self, answer: str) -> bool:
        return self._classify_clarify_request(answer) is not None

    def _build_meaning_clarify_prompt(self, question: str) -> str:
        core = (question or "").strip()
        if not core:
            return "我复述一下题目：请按题目本身作答。这里需要围绕题干要求说明你的真实做法。请按这个题意继续作答。"
        return (
            f"我复述一下题目：{core}。"
            "这里的关键术语按题干上下文理解，不需要额外扩展范围。"
            "请按这个题意继续作答。"
        )
