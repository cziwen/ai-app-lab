#!/usr/bin/env python3
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from interview_flow import ASK_FOLLOWUP, ASK_QUESTION, DONE, INTRO, WAIT_ANSWER, WRAP_UP, InterviewFlow
from interview_judge import InterviewJudge

DEFAULT_LLM_ENDPOINT_ID = "ep-m-20260315140910-pfztd"

QUESTIONS: List[Dict[str, Any]] = [
    {
        "question_id": "q1",
        "main_question": "请用1分钟做自我介绍，重点说与你申请岗位相关的经历。",
        "evidence": {"scoring_boundary": "是否清晰说明岗位相关经历，并体现与岗位匹配度"},
    },
    {
        "question_id": "q2",
        "main_question": "请介绍一个你主导的项目，说明目标、你的动作和结果。",
        "evidence": {"scoring_boundary": "是否完整覆盖项目目标、个人关键动作和可验证结果"},
    },
    {
        "question_id": "q3",
        "main_question": "当你遇到复杂问题时，通常如何拆解并推动解决？",
        "evidence": {"scoring_boundary": "是否体现问题拆解方法、推进机制和复盘意识"},
    },
]

CANDIDATE_ANSWERS: List[str] = [
    "我叫小陈，做过两个AI应用项目。",
    "我负责了从需求拆解到上线推进，跟产品和前端一起把版本做出来。",
    "项目目标是把用户留存提高10%，我负责方案设计和落地，最后提升了12%。",
    "遇到复杂问题我会先拆成技术、业务、时间三个维度，再排优先级推进。",
    "过程中会拉齐干系人，做里程碑检查，最后做复盘沉淀。",
    "补充一点，我会用指标来验证方案是否有效。",
]


async def main() -> int:
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        print("ERROR: missing ARK_API_KEY")
        return 1

    endpoint_id = os.environ.get("LLM_ENDPOINT_ID", DEFAULT_LLM_ENDPOINT_ID)

    judge = InterviewJudge(
        llm_endpoint_id=endpoint_id,
        max_followups_per_question=2,
        coverage_threshold=0.7,
    )
    flow = InterviewFlow(
        questions=QUESTIONS,
        judge=judge,
        max_followups_per_question=2,
        global_turn_limit=20,
    )

    lines: List[str] = []
    lines.append(f"Simulation Timestamp: {datetime.now().isoformat()}")
    lines.append(f"LLM Endpoint: {endpoint_id}")
    lines.append("=" * 80)

    answer_idx = 0
    safety_loops = 0

    while not flow.is_done and safety_loops < 100:
        safety_loops += 1

        if flow.state in {INTRO, ASK_QUESTION, ASK_FOLLOWUP, WRAP_UP}:
            out = await flow.produce_interviewer_message()
            if out.interviewer_text:
                lines.append(
                    f"[Interviewer][{out.state_before}->{out.state_after}] q={out.question_id or '-'}: {out.interviewer_text}"
                )
            for t in out.transition_trace:
                lines.append(f"  transition: {t}")
            continue

        if flow.state == WAIT_ANSWER:
            if answer_idx < len(CANDIDATE_ANSWERS):
                answer = CANDIDATE_ANSWERS[answer_idx]
            else:
                answer = "我补充完了，我的核心思路是目标导向并按数据闭环。"
            answer_idx += 1

            lines.append(f"[Candidate]: {answer}")
            result = await flow.receive_candidate_answer(answer)
            decision = result.decision
            if decision is not None:
                lines.append(
                    "[Decision] "
                    f"move_forward={decision.move_forward} "
                    f"need_follow_up={decision.need_follow_up} "
                    f"coverage={decision.coverage_score:.2f} "
                    f"reason={decision.reason}"
                )
                if decision.follow_up_question:
                    lines.append(f"[FollowUpQuestion] {decision.follow_up_question}")
            for t in result.transition_trace:
                lines.append(f"  transition: {t}")
            continue

        lines.append(f"[WARN] unexpected state: {flow.state}")
        break

    lines.append("=" * 80)
    lines.append(f"Final state: {flow.state}")

    output_dir = Path(__file__).resolve().parent / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "interview_simulation.txt"
    output_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Simulation written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
