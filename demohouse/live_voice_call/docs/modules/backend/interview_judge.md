# backend/interview_judge.py

## 模块概述
`InterviewJudge` 负责评估候选人回答并返回下一步动作（下一题 / 追问 / 澄清）。

## Decision 契约（当前实现）
```python
@dataclass
class Decision:
    next_action: str      # next_question | follow_up | clarify
    next_prompt: str
    reason: str
    coverage_score: float
```

兼容说明：
- 代码中的 `_normalize()` 仍可解析历史字段（`move_forward` / `need_follow_up` / `follow_up_question`），仅用于兼容旧模型输出。
- 对外文档与新逻辑应统一使用 `next_action`/`next_prompt`。

## 决策规则
- 候选人明确“没听懂/请解释题目”优先走 `clarify`。
- 回答过短优先走 `follow_up`。
- 其余情况下根据 LLM 或启发式覆盖度判断 `next_question` 或 `follow_up`。

## LLM 输出要求
`JUDGE_INSTRUCTIONS` 要求 LLM 输出 JSON，字段必须包含：
- `next_action`
- `next_prompt`
- `reason`
- `coverage_score`

## 相关测试
- `pytest backend/tests/test_interview_judge.py`
- `pytest backend/tests/test_interview_flow.py`
