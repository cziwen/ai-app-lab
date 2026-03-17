from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from interview_judge import Decision, InterviewJudge


INTRO = "INTRO"
ASK_QUESTION = "ASK_QUESTION"
WAIT_ANSWER = "WAIT_ANSWER"
EVAL_ANSWER = "EVAL_ANSWER"
DECIDE = "DECIDE"
ASK_FOLLOWUP = "ASK_FOLLOWUP"
WRAP_UP = "WRAP_UP"
DONE = "DONE"


@dataclass
class QuestionContext:
    question_id: str
    main_question: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    follow_up_count: int = 0
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


class InterviewFlow:
    def __init__(
        self,
        questions: List[Dict[str, Any]],
        judge: InterviewJudge,
        max_followups_per_question: int = 2,
        global_turn_limit: int = 20,
    ):
        if not questions:
            raise ValueError("questions must not be empty")

        self.judge = judge
        self.max_followups_per_question = max_followups_per_question
        self.global_turn_limit = global_turn_limit

        self.questions: List[QuestionContext] = [
            QuestionContext(
                question_id=str(item["question_id"]),
                main_question=str(item["main_question"]),
                evidence=dict(item.get("evidence") or {}),
            )
            for item in questions
        ]

        self.state = INTRO
        self.current_question_index = 0
        self.total_candidate_turns = 0
        self.latest_follow_up: str = ""

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
                interviewer_text="你好，欢迎参加面试。全程3道题，我会根据你的回答决定是否追问。",
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
            return FlowResponse(
                state_before=before,
                state_after=self.state,
                interviewer_text=self.latest_follow_up,
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

        q.turns.append({"role": "candidate", "content": answer})
        self.total_candidate_turns += 1

        trace.append(f"{WAIT_ANSWER} -> {EVAL_ANSWER}")
        self.state = EVAL_ANSWER

        trace.append(f"{EVAL_ANSWER} -> {DECIDE}")
        self.state = DECIDE

        # Guard: global limit -> force wrap up.
        if self.total_candidate_turns >= self.global_turn_limit:
            forced = Decision(
                move_forward=True,
                need_follow_up=False,
                follow_up_question="",
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
            evidence=q.evidence,
        )
        q.coverage_score = decision.coverage_score

        if decision.need_follow_up and not decision.move_forward:
            q.follow_up_count += 1
            self.latest_follow_up = decision.follow_up_question
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
