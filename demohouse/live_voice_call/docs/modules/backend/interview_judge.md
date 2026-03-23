# backend/interview_judge.py

## 模块概述

InterviewJudge 是面试评判器（LLM1），负责评估候选人回答质量并决定是否追问。支持 LLM 模式和启发式模式。

## 核心类

### Decision 数据类

```python
@dataclass
class Decision:
    move_forward: bool          # 是否进入下一题
    need_follow_up: bool        # 是否需要追问
    follow_up_question: str     # 追问内容
    reason: str                 # 决策理由
    coverage_score: float       # 覆盖度得分（0~1）
```

### InterviewJudge 评判器

```python
class InterviewJudge:
    def __init__(
        self,
        coverage_threshold: float = 0.7,           # 覆盖度阈值
        max_followups_per_question: int = 1,      # 单题最大追问次数
        llm_endpoint_id: Optional[str] = None,    # LLM端点（None则用启发式）
        llm_thinking_type: str = "disabled",      # 思维链模式
        llm_reasoning_effort: Optional[str] = None,  # 推理强度
        responses_adapter: Optional[ArkResponsesAdapter] = None,
    )
```

## 评判流程

### decide() 主流程

```python
async def decide(
    question: str,              # 面试题
    candidate_answer: str,      # 候选人回答
    follow_up_count: int,       # 已追问次数
    evidence: Optional[Dict],   # 评分依据（scoring_boundary）
) -> Decision:
    # Guard 1：追问次数上限
    if follow_up_count >= self.max_followups_per_question:
        return Decision(move_forward=True, ...)

    # Guard 2：回答过短
    if len(answer) < 6:
        return Decision(need_follow_up=True, 
            follow_up_question="请你结合一个具体经历，再展开说明一下。", ...)

    # 调用 LLM 或启发式算法
    raw = await self._call_llm(question, answer, follow_up_count, evidence)
    parsed = self._parse_json(raw)
    return self._normalize(parsed)
```

## LLM 模式

### System Prompt

```python
JUDGE_INSTRUCTIONS = """
你是结构化面试裁决器。只输出一个JSON对象，不要输出任何额外文字。
JSON字段必须包含：move_forward(bool), need_follow_up(bool), 
follow_up_question(str), reason(str), coverage_score(float 0~1)。
遵守：当 need_follow_up=true 时，follow_up_question 必须非空；
当 move_forward=true 时，need_follow_up 必须为 false。
评分时只允许参考：题目(question)、评分分界线(scoring_boundary)、候选人回答(candidate_answer)。
禁止参考 must_cover、reference_answer、best_standard、medium_standard、worst_standard。
"""
```

### 输入格式

```python
prompt_payload = {
    "question": "请介绍一个你主导的项目...",
    "scoring_boundary": "是否包含项目目标、个人动作、可量化结果",
    "candidate_answer": "我在2023年负责了一个推荐系统...",
    "follow_up_count": 0,
    "instruction": "仅基于 question 与 scoring_boundary 评估..."
}
```

### 输出示例

```json
{
    "move_forward": false,
    "need_follow_up": true,
    "follow_up_question": "你提到了推荐系统，能否具体说明项目的业务目标是什么？",
    "reason": "候选人提及了项目背景，但未明确说明项目目标和个人关键动作",
    "coverage_score": 0.4
}
```

## 启发式模式

### 关键词提取

```python
def _extract_keywords(text: str) -> List[str]:
    # 提取中文词汇（2+字符）和英文词汇
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", normalized)
    
    # 对长词切分bigram
    if len(token) >= 4:
        bigrams = [token[i:i+2] for i in range(len(token)-1)]
    
    # 最多提取16个关键词
    return picked[:16]
```

### 覆盖度计算

```python
def _heuristic_decision_json(...) -> str:
    rubric_keywords = _extract_keywords(f"{question} {scoring_boundary}")
    answer_keywords = _extract_keywords(candidate_answer)
    
    # 计算关键词重叠度
    overlap_count = len(set(rubric_keywords) & set(answer_keywords))
    coverage = overlap_count / len(rubric_keywords)
    
    # 长文本补偿
    if coverage < threshold and len(candidate_answer) >= 80:
        coverage = max(coverage, threshold - 0.05)
    
    # 决策
    if coverage >= threshold:
        return {"move_forward": True, "need_follow_up": False, ...}
    else:
        return {"move_forward": False, "need_follow_up": True, ...}
```

## 决策归一化

### _normalize() 方法

处理 LLM 输出的冲突情况：

```python
def _normalize(payload: Dict) -> Decision:
    # 冲突解决：move_forward 和 need_follow_up 同时为 True
    if move_forward and need_follow_up:
        if coverage_score >= self.coverage_threshold:
            need_follow_up = False  # 优先前进
        else:
            move_forward = False    # 优先追问

    # 双False兜底
    if (not move_forward) and (not need_follow_up):
        need_follow_up = True

    # move_forward=True 则清空追问
    if move_forward:
        need_follow_up = False
        follow_up_question = ""

    # need_follow_up=True 但追问为空则填充默认
    if need_follow_up and not follow_up_question:
        follow_up_question = "请你补充一个更具体的案例和结果。"
```

## 配置优化

### 降低延迟

```python
# 禁用思维链
llm_thinking_type="disabled"

# 使用低推理强度
llm_reasoning_effort="low"

# 或完全使用启发式
llm_endpoint_id=None  # 触发启发式模式
```

### 提高准确率

```python
# 启用思维链
llm_thinking_type="enabled"

# 使用高推理强度
llm_reasoning_effort="high"

# 降低覆盖度阈值（更宽松）
coverage_threshold=0.6
```

## 故障排查

### 1. 一直追问不停

**原因**：LLM 持续返回 `need_follow_up=True`

**解决**：
```python
# 检查 max_followups_per_question 是否生效
print(judge.max_followups_per_question)

# 检查 Guard 逻辑
if follow_up_count >= max_followups_per_question:
    print("Should force move_forward")
```

### 2. 从不追问

**原因**：coverage_score 一直很高

**解决**：
```python
# 提高阈值
coverage_threshold=0.85

# 或检查 LLM 输出
print(decision.coverage_score, decision.reason)
```

### 3. JSON 解析失败

**原因**：LLM 返回非 JSON 文本

**解决**：
- 检查 JUDGE_INSTRUCTIONS 是否正确设置
- 使用更强的 LLM 模型
- 降级到启发式模式

## 相关测试

```bash
pytest backend/tests/test_interview_judge_llm.py
pytest backend/tests/test_interview_judge_heuristic.py
pytest backend/tests/test_interview_judge_normalize.py
```
