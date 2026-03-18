import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from arkitect.core.component.llm import BaseChatLanguageModel
from arkitect.core.component.llm.model import ArkMessage
from langchain.prompts.chat import BaseChatPromptTemplate
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage, SystemMessage


@dataclass
class Decision:
    move_forward: bool
    need_follow_up: bool
    follow_up_question: str
    reason: str
    coverage_score: float


class JudgePrompt(BaseChatPromptTemplate):
    input_variables: List[str] = ["messages"]

    def format_messages(self, **kwargs: Any) -> List[BaseMessage]:
        messages: List[AnyMessage] = kwargs["messages"]
        system = SystemMessage(
            content=(
                "你是结构化面试裁决器。只输出一个JSON对象，不要输出任何额外文字。"
                "JSON字段必须包含：move_forward(bool), need_follow_up(bool), "
                "follow_up_question(str), reason(str), coverage_score(float 0~1)。"
                "遵守：当 need_follow_up=true 时，follow_up_question 必须非空；"
                "当 move_forward=true 时，need_follow_up 必须为 false。"
            )
        )
        return [system] + messages


class InterviewJudge:
    def __init__(
        self,
        coverage_threshold: float = 0.7,
        max_followups_per_question: int = 1,
        llm_endpoint_id: Optional[str] = None,
        llm_decider: Optional[
            Callable[[str, str, int, Optional[Dict[str, Any]]], Awaitable[str]]
        ] = None,
    ):
        self.coverage_threshold = coverage_threshold
        self.max_followups_per_question = max_followups_per_question
        self.llm_endpoint_id = llm_endpoint_id
        self.llm_decider = llm_decider

    async def decide(
        self,
        question: str,
        candidate_answer: str,
        follow_up_count: int,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Decision:
        answer = (candidate_answer or "").strip()

        # Guard 1: follow-up upper bound -> force move forward.
        if follow_up_count >= self.max_followups_per_question:
            return Decision(
                move_forward=True,
                need_follow_up=False,
                follow_up_question="",
                reason="follow_up_limit_reached",
                coverage_score=1.0,
            )

        # Guard 2: empty/too short answer -> prefer follow-up.
        if len(answer) < 6:
            return Decision(
                move_forward=False,
                need_follow_up=True,
                follow_up_question="请你结合一个具体经历，再展开说明一下。",
                reason="answer_too_short",
                coverage_score=0.0,
            )

        raw = await self._call_llm(question, answer, follow_up_count, evidence)
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
        evidence: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if self.llm_decider is not None:
            return await self.llm_decider(
                question, candidate_answer, follow_up_count, evidence
            )

        if not self.llm_endpoint_id:
            return self._heuristic_decision_json(question, candidate_answer, evidence)

        prompt_payload = {
            "question": question,
            "candidate_answer": candidate_answer,
            "follow_up_count": follow_up_count,
            "evidence": evidence or {},
            "instruction": (
                "根据回答完整度决定 move_forward 或 need_follow_up。"
                "若需要追问，给出一个具体追问。仅返回JSON。"
            ),
        }
        llm = BaseChatLanguageModel(
            template=JudgePrompt(),
            messages=[ArkMessage(**{"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)})],
            endpoint_id=self.llm_endpoint_id,
        )

        chunks: List[str] = []
        async for chunk in llm.astream():
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                chunks.append(chunk.choices[0].delta.content)

        if not chunks:
            return None
        return "".join(chunks)

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
        move_forward = bool(payload.get("move_forward", False))
        need_follow_up = bool(payload.get("need_follow_up", True))
        follow_up_question = str(payload.get("follow_up_question", "") or "")
        reason = str(payload.get("reason", "llm_decision") or "llm_decision")

        try:
            coverage_score = float(payload.get("coverage_score", 0.0))
        except (TypeError, ValueError):
            coverage_score = 0.0
        coverage_score = max(0.0, min(1.0, coverage_score))

        # Resolve conflicting output deterministically.
        if move_forward and need_follow_up:
            if coverage_score >= self.coverage_threshold:
                need_follow_up = False
            else:
                move_forward = False

        if (not move_forward) and (not need_follow_up):
            need_follow_up = True

        if move_forward:
            need_follow_up = False
            follow_up_question = ""

        if need_follow_up and not follow_up_question:
            follow_up_question = "请你补充一个更具体的案例和结果。"

        return Decision(
            move_forward=move_forward,
            need_follow_up=need_follow_up,
            follow_up_question=follow_up_question,
            reason=reason,
            coverage_score=coverage_score,
        )

    def _fallback_follow_up(self, reason: str) -> Decision:
        return Decision(
            move_forward=False,
            need_follow_up=True,
            follow_up_question="我想听到更具体的行动和结果，请再展开一下。",
            reason=reason,
            coverage_score=0.0,
        )

    def _heuristic_decision_json(
        self, question: str, candidate_answer: str, evidence: Optional[Dict[str, Any]]
    ) -> str:
        must_cover = []
        if evidence and isinstance(evidence.get("must_cover"), list):
            must_cover = [str(x) for x in evidence["must_cover"] if str(x).strip()]

        hit_count = 0
        for keyword in must_cover:
            if keyword in candidate_answer:
                hit_count += 1

        coverage = 0.5
        if must_cover:
            coverage = hit_count / len(must_cover)
        elif len(candidate_answer) >= 40:
            coverage = 0.8

        if coverage >= self.coverage_threshold:
            decision = {
                "move_forward": True,
                "need_follow_up": False,
                "follow_up_question": "",
                "reason": "heuristic_enough_coverage",
                "coverage_score": coverage,
            }
        else:
            decision = {
                "move_forward": False,
                "need_follow_up": True,
                "follow_up_question": "请补充你具体做了什么，以及最终结果如何。",
                "reason": "heuristic_need_more_detail",
                "coverage_score": coverage,
            }

        return json.dumps(decision, ensure_ascii=False)
