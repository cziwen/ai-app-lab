from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from interview_judge import Decision, InterviewJudge


INTRO = "INTRO"
ASK_QUESTION = "ASK_QUESTION"
WAIT_ANSWER = "WAIT_ANSWER"
EVAL_ANSWER = "EVAL_ANSWER"
DECIDE = "DECIDE"
ASK_FOLLOWUP = "ASK_FOLLOWUP"
ASK_CLARIFY = "ASK_CLARIFY"
WRAP_UP = "WRAP_UP"
DONE = "DONE"


@dataclass
class QuestionContext:
    question_id: str
    main_question: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    question_epoch: int = 0
    max_followups: int = 0
    follow_up_count: int = 0
    max_clarifies: int = 0
    clarify_count: int = 0
    coverage_score: float = 0.0
    turns: List[Dict[str, str]] = field(default_factory=list)
    status: str = "pending"  # pending | in_progress | done


@dataclass
class FlowResponse:
    state_before: str
    state_after: str
    interviewer_text: str
    decision: Optional[Decision]
    question_id: Optional[str]
    transition_trace: List[str]


@dataclass
class QuestionAnswerSnapshot:
    question_id: str
    sort_order: int
    question: str
    evidence: Dict[str, Any]
    candidate_answers: List[str]
    aggregated_answer: str


class InterviewFlow:
    def __init__(
        self,
        questions: List[Dict[str, Any]],
        judge: InterviewJudge,
        global_turn_limit: int = 20,
    ):
        if not questions:
            raise ValueError("questions must not be empty")

        self.judge = judge
        self.global_turn_limit = global_turn_limit

        self.questions: List[QuestionContext] = [
            QuestionContext(
                question_id=str(item["question_id"]),
                main_question=str(item["main_question"]),
                evidence=dict(item.get("evidence") or {}),
                max_followups=max(0, int(item.get("max_followups", 0))),
                max_clarifies=max(0, int(item.get("max_clarifies", 1))),
            )
            for item in questions
        ]
        self.total_questions = len(self.questions)
        self.total_scenarios = self._count_scenarios(questions)

        self.state = INTRO
        self.current_question_index = 0
        self.total_candidate_turns = 0
        self.latest_follow_up: str = ""
        self.latest_clarify: str = ""

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def export_runtime_checkpoint(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "state": self.state,
            "current_question_index": self.current_question_index,
            "total_candidate_turns": self.total_candidate_turns,
            "latest_follow_up": self.latest_follow_up,
            "latest_clarify": self.latest_clarify,
            "questions": [
                {
                    "question_id": q.question_id,
                    "status": q.status,
                    "question_epoch": int(q.question_epoch or 0),
                    "follow_up_count": q.follow_up_count,
                    "clarify_count": q.clarify_count,
                    "coverage_score": q.coverage_score,
                    "turns": [
                        {
                            "role": str(turn.get("role", "")),
                            "content": str(turn.get("content", "")),
                            "created_at": str(turn.get("created_at", "") or ""),
                        }
                        for turn in q.turns
                        if isinstance(turn, dict)
                    ],
                }
                for q in self.questions
            ],
        }

    def restore_runtime_checkpoint(self, checkpoint: Dict[str, Any]) -> bool:
        if not isinstance(checkpoint, dict):
            return False
        raw_questions = checkpoint.get("questions")
        if not isinstance(raw_questions, list):
            return False
        if len(raw_questions) != len(self.questions):
            return False

        restored_turn_count = 0
        for idx, raw_q in enumerate(raw_questions):
            if not isinstance(raw_q, dict):
                return False
            q = self.questions[idx]
            status = str(raw_q.get("status", q.status) or q.status).strip()
            if status not in {"pending", "in_progress", "done"}:
                status = q.status
            q.status = status
            try:
                q.question_epoch = max(0, int(raw_q.get("question_epoch", 0) or 0))
            except (TypeError, ValueError):
                q.question_epoch = 0
            q.follow_up_count = max(0, int(raw_q.get("follow_up_count", 0) or 0))
            q.clarify_count = max(0, int(raw_q.get("clarify_count", 0) or 0))
            try:
                q.coverage_score = float(raw_q.get("coverage_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                q.coverage_score = 0.0
            q.turns = []
            raw_turns = raw_q.get("turns")
            if isinstance(raw_turns, list):
                for turn in raw_turns:
                    if not isinstance(turn, dict):
                        continue
                    role = str(turn.get("role", "") or "").strip()
                    content = str(turn.get("content", "") or "").strip()
                    if role not in {"candidate", "interviewer"}:
                        continue
                    if not content:
                        continue
                    created_at = str(turn.get("created_at", "") or "").strip()
                    q.turns.append(
                        {
                            "role": role,
                            "content": content,
                            "created_at": created_at or self._utc_now_iso(),
                        }
                    )
                    if role == "candidate":
                        restored_turn_count += 1

        max_index = len(self.questions) - 1
        try:
            restored_index = int(checkpoint.get("current_question_index", 0) or 0)
        except (TypeError, ValueError):
            restored_index = 0
        self.current_question_index = min(max(restored_index, 0), max_index)
        try:
            restored_total_turns = int(checkpoint.get("total_candidate_turns", 0) or 0)
        except (TypeError, ValueError):
            restored_total_turns = 0
        self.total_candidate_turns = max(restored_total_turns, restored_turn_count)
        self.latest_follow_up = str(checkpoint.get("latest_follow_up", "") or "")
        self.latest_clarify = str(checkpoint.get("latest_clarify", "") or "")

        state = str(checkpoint.get("state", self.state) or self.state).strip()
        allowed_states = {
            INTRO,
            ASK_QUESTION,
            WAIT_ANSWER,
            EVAL_ANSWER,
            DECIDE,
            ASK_FOLLOWUP,
            ASK_CLARIFY,
            WRAP_UP,
            DONE,
        }
        if state in allowed_states:
            self.state = state
        return True

    def rewind_to_question_start(self, index: int) -> bool:
        if index < 0 or index >= len(self.questions):
            return False
        q = self.questions[index]
        if q.status == "done":
            return False
        removed_candidate_turns = sum(
            1 for turn in q.turns if str(turn.get("role", "")) == "candidate"
        )
        q.turns = []
        q.question_epoch = int(q.question_epoch or 0) + 1
        q.follow_up_count = 0
        q.clarify_count = 0
        q.coverage_score = 0.0
        q.status = "pending"
        self.current_question_index = index
        self.total_candidate_turns = max(
            0, int(self.total_candidate_turns) - removed_candidate_turns
        )
        self.latest_follow_up = ""
        self.latest_clarify = ""
        self.state = ASK_QUESTION
        return True

    @staticmethod
    def _count_scenarios(questions: List[Dict[str, Any]]) -> int:
        count = 0
        previous_scene = ""
        for item in questions:
            scenario = str(item.get("scenario", "") or "").strip()
            evidence = item.get("evidence")
            if not scenario and isinstance(evidence, dict):
                scenario = str(evidence.get("scenario", "") or "").strip()

            if scenario:
                if scenario != previous_scene:
                    count += 1
            else:
                # Empty scenario is treated as a standalone segment per question.
                count += 1
            previous_scene = scenario
        return count

    @property
    def is_done(self) -> bool:
        return self.state == DONE

    def _current_question(self) -> QuestionContext:
        return self.questions[self.current_question_index]

    async def produce_interviewer_message(self) -> FlowResponse:
        trace: List[str] = []
        before = self.state

        if self.state == INTRO:
            trace.append(f"{INTRO} -> {ASK_QUESTION}")
            self.state = ASK_QUESTION
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text=(
                    f"你好，欢迎参加面试。本场共{self.total_scenarios}个场景，"
                    "我会围绕每个场景视情况逐步提问，必要时也会澄清题意。"
                ),
                decision=None,
                question_id=None,
                transition_trace=trace,
            )

        if self.state == ASK_QUESTION:
            q = self._current_question()
            if q.status == "pending":
                q.status = "in_progress"
            trace.append(f"{ASK_QUESTION} -> {WAIT_ANSWER}")
            self.state = WAIT_ANSWER
            if q.main_question:
                q.turns.append(
                    {
                        "role": "interviewer",
                        "content": q.main_question,
                        "created_at": self._utc_now_iso(),
                    }
                )
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text=q.main_question,
                decision=None,
                question_id=q.question_id,
                transition_trace=trace,
            )

        if self.state == ASK_FOLLOWUP:
            q = self._current_question()
            trace.append(f"{ASK_FOLLOWUP} -> {WAIT_ANSWER}")
            self.state = WAIT_ANSWER
            if self.latest_follow_up:
                q.turns.append(
                    {
                        "role": "interviewer",
                        "content": self.latest_follow_up,
                        "created_at": self._utc_now_iso(),
                    }
                )
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text=self.latest_follow_up,
                decision=None,
                question_id=q.question_id,
                transition_trace=trace,
            )

        if self.state == ASK_CLARIFY:
            q = self._current_question()
            trace.append(f"{ASK_CLARIFY} -> {WAIT_ANSWER}")
            self.state = WAIT_ANSWER
            if self.latest_clarify:
                q.turns.append(
                    {
                        "role": "interviewer",
                        "content": self.latest_clarify,
                        "created_at": self._utc_now_iso(),
                    }
                )
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text=self.latest_clarify,
                decision=None,
                question_id=q.question_id,
                transition_trace=trace,
            )

        if self.state == WRAP_UP:
            trace.append(f"{WRAP_UP} -> {DONE}")
            self.state = DONE
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text="好的，面试到这里结束。感谢你的时间，我们会尽快给出反馈。",
                decision=None,
                question_id=None,
                transition_trace=trace,
            )

        if self.state == DONE:
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text="",
                decision=None,
                question_id=None,
                transition_trace=trace,
            )

        raise RuntimeError(f"state {self.state} does not produce interviewer message")

    async def receive_candidate_answer(self, answer: str) -> FlowResponse:
        if self.state != WAIT_ANSWER:
            raise RuntimeError(f"expected {WAIT_ANSWER}, got {self.state}")

        trace: List[str] = []
        before = self.state
        q = self._current_question()

        q.turns.append(
            {
                "role": "candidate",
                "content": answer,
                "created_at": self._utc_now_iso(),
            }
        )
        self.total_candidate_turns += 1

        trace.append(f"{WAIT_ANSWER} -> {EVAL_ANSWER}")
        self.state = EVAL_ANSWER

        trace.append(f"{EVAL_ANSWER} -> {DECIDE}")
        self.state = DECIDE

        # Guard: global limit -> force wrap up.
        if self.total_candidate_turns >= self.global_turn_limit:
            forced = Decision(
                next_action="next_question",
                next_prompt="",
                reason="global_turn_limit_reached",
                coverage_score=q.coverage_score,
            )
            trace.append(f"{DECIDE} -> {WRAP_UP}")
            self.state = WRAP_UP
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text="",
                decision=forced,
                question_id=q.question_id,
                transition_trace=trace,
            )

        decision = await self.judge.decide(
            question=q.main_question,
            candidate_answer=answer,
            follow_up_count=q.follow_up_count,
            clarify_count=q.clarify_count,
            evidence=q.evidence,
        )
        if decision.next_action == "follow_up" and q.follow_up_count >= q.max_followups:
            decision = Decision(
                next_action="next_question",
                next_prompt="",
                reason="follow_up_limit_reached",
                coverage_score=q.coverage_score,
            )
        if decision.next_action == "clarify" and q.clarify_count >= q.max_clarifies:
            decision = Decision(
                next_action="next_question",
                next_prompt="",
                reason="clarify_limit_reached",
                coverage_score=q.coverage_score,
            )
        q.coverage_score = decision.coverage_score

        if decision.next_action == "follow_up":
            q.follow_up_count += 1
            self.latest_follow_up = decision.next_prompt
            trace.append(f"{DECIDE} -> {ASK_FOLLOWUP}")
            self.state = ASK_FOLLOWUP
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text="",
                decision=decision,
                question_id=q.question_id,
                transition_trace=trace,
            )

        if decision.next_action == "clarify":
            q.clarify_count += 1
            self.latest_clarify = decision.next_prompt
            trace.append(f"{DECIDE} -> {ASK_CLARIFY}")
            self.state = ASK_CLARIFY
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text="",
                decision=decision,
                question_id=q.question_id,
                transition_trace=trace,
            )

        # move forward path
        q.status = "done"
        if self.current_question_index >= len(self.questions) - 1:
            trace.append(f"{DECIDE} -> {WRAP_UP}")
            self.state = WRAP_UP
        else:
            self.current_question_index += 1
            trace.append(f"{DECIDE} -> {ASK_QUESTION}")
            self.state = ASK_QUESTION

        return FlowResponse(
            state_before=before,
            state_after=self.state,
            interviewer_text="",
            decision=decision,
            question_id=q.question_id,
            transition_trace=trace,
        )

    def export_question_answer_snapshots(self) -> List[QuestionAnswerSnapshot]:
        snapshots: List[QuestionAnswerSnapshot] = []
        for idx, q in enumerate(self.questions):
            candidate_answers = [
                str(turn.get("content", "")).strip()
                for turn in q.turns
                if str(turn.get("role", "")) == "candidate" and str(turn.get("content", "")).strip()
            ]
            aggregated = "\n".join(candidate_answers).strip()
            snapshots.append(
                QuestionAnswerSnapshot(
                    question_id=q.question_id,
                    sort_order=idx + 1,
                    question=q.main_question,
                    evidence=dict(q.evidence or {}),
                    candidate_answers=candidate_answers,
                    aggregated_answer=aggregated,
                )
            )
        return snapshots
