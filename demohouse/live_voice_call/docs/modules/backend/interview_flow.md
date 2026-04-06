# backend/interview_flow.py

## 模块概述
`interview_flow.py` 实现结构化面试状态机，负责主问题、追问、澄清、结束语等状态推进。

## 状态机（当前为 9 状态）
- `INTRO`
- `ASK_QUESTION`
- `WAIT_ANSWER`
- `EVAL_ANSWER`
- `DECIDE`
- `ASK_FOLLOWUP`
- `ASK_CLARIFY`
- `WRAP_UP`
- `DONE`

典型路径：
- 开场：`INTRO -> ASK_QUESTION -> WAIT_ANSWER`
- 追问：`DECIDE -> ASK_FOLLOWUP -> WAIT_ANSWER`
- 澄清：`DECIDE -> ASK_CLARIFY -> WAIT_ANSWER`
- 收尾：`DECIDE -> WRAP_UP -> DONE`

## 决策输入/输出
状态机通过 `InterviewJudge.decide(...)` 返回 `Decision`：
- `next_action`: `next_question | follow_up | clarify`
- `next_prompt`: 下一步问题文本（`next_question` 时应为空）
- `reason`
- `coverage_score`

## 限制与 Guard
- 全局轮次上限：`global_turn_limit`，达到后强制进入 `WRAP_UP`。
- 单题追问上限：`max_followups`，超限后强制 `next_question`。
- 单题澄清上限：`max_clarifies`，超限后强制 `next_question`。

## 关键数据结构

### `QuestionContext`
```python
@dataclass
class QuestionContext:
    question_id: str
    main_question: str
    evidence: Dict[str, Any]
    max_followups: int = 0
    follow_up_count: int = 0
    max_clarifies: int = 0
    clarify_count: int = 0
    coverage_score: float = 0.0
    turns: List[Dict[str, str]]
    status: str = "pending"
```

### `FlowResponse`
```python
@dataclass
class FlowResponse:
    state_before: str
    state_after: str
    interviewer_text: str
    decision: Optional[Decision]
    question_id: Optional[str]
    transition_trace: List[str]
```

## 相关测试
- `pytest backend/tests/test_interview_flow.py`
- `pytest backend/tests/test_service_interview_llm.py`
