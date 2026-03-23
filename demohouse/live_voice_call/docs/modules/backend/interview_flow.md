# backend/interview_flow.py

## 模块概述

`interview_flow.py` 实现了结构化面试的状态机核心逻辑，负责协调面试各个阶段的状态转换、问题推进、追问决策和结束控制。该模块是面试模式下的核心编排器，与 `InterviewJudge` 协同工作完成面试流程控制。

## 核心职责

1. 维护面试状态机，管理 8 个状态之间的转换
2. 控制问题进度和追问逻辑
3. 强制执行全局轮次限制和单题追问上限
4. 生成面试官消息（开场白、问题、结束语）
5. 接收并处理候选人回答
6. 协调 InterviewJudge 进行回答评估

## 状态机设计

### 状态定义

面试流程包含 8 个状态，形成严格的有向图：

```
状态常量                  含义                    可转换到
-------------------------------------------------------------
INTRO                 开场白                   ASK_QUESTION
ASK_QUESTION          提出主问题                WAIT_ANSWER
WAIT_ANSWER           等待候选人回答             EVAL_ANSWER
EVAL_ANSWER           评估回答（瞬态）           DECIDE
DECIDE                决策下一步（瞬态）         ASK_FOLLOWUP / ASK_QUESTION / WRAP_UP
ASK_FOLLOWUP          提出追问                  WAIT_ANSWER
WRAP_UP               结束语                    DONE
DONE                  面试完成（终态）           无
```

### 状态转换规则

**1. 开场流程**
```
INTRO -> ASK_QUESTION -> WAIT_ANSWER
```

**2. 正常回答后评估**
```
WAIT_ANSWER -> EVAL_ANSWER -> DECIDE
```

**3. 决策分支**

从 `DECIDE` 状态出发有三种可能：

- **追问路径**：回答不充分且未达追问上限
  ```
  DECIDE -> ASK_FOLLOWUP -> WAIT_ANSWER
  ```

- **下一题路径**：回答充分，进入下一题
  ```
  DECIDE -> ASK_QUESTION -> WAIT_ANSWER
  ```

- **结束路径**：最后一题完成或触发全局限制
  ```
  DECIDE -> WRAP_UP -> DONE
  ```

### 强制转换条件

以下条件会强制触发状态转换，优先级高于 Judge 决策：

1. **全局轮次限制**（`global_turn_limit`）：
   - 当 `total_candidate_turns >= global_turn_limit` 时强制 `DECIDE -> WRAP_UP`
   - 原因标记：`global_turn_limit_reached`

2. **单题追问上限**（`max_followups_per_question`）：
   - 当 `follow_up_count >= max_followups_per_question` 时强制下一题
   - 通过 InterviewJudge 的 Guard 逻辑实现

3. **题库耗尽**：
   - 当 `current_question_index >= len(questions) - 1` 且当前题完成时触发 `WRAP_UP`

## 核心类与数据结构

### QuestionContext

问题上下文，记录单个问题的完整状态：

```python
@dataclass
class QuestionContext:
    question_id: str              # 问题唯一标识
    main_question: str            # 主问题文本
    evidence: Dict[str, Any]      # 评分依据（如 scoring_boundary）
    follow_up_count: int          # 已追问次数
    coverage_score: float         # 覆盖度得分（0.0~1.0）
    turns: List[Dict[str, str]]   # 对话轮次记录
    status: str                   # pending | in_progress | done
```

**字段说明**：
- `evidence`：传递给 Judge 的评分标准，通常包含 `scoring_boundary`
- `coverage_score`：由 Judge 返回，表示回答对问题的覆盖程度
- `turns`：记录完整对话历史，每个元素为 `{"role": "candidate", "content": "..."}`

### FlowResponse

状态转换响应，记录单次状态变化的完整信息：

```python
@dataclass
class FlowResponse:
    state_before: str               # 转换前状态
    state_after: str                # 转换后状态
    interviewer_text: str           # 面试官要说的话（可能为空）
    decision: Optional[Decision]    # Judge 决策结果（仅回答处理后有值）
    question_id: Optional[str]      # 当前问题 ID
    transition_trace: List[str]     # 转换路径追踪（如 ["WAIT_ANSWER -> EVAL_ANSWER"]）
```

**使用场景**：
- `produce_interviewer_message()`：生成面试官主动消息时返回
- `receive_candidate_answer()`：处理候选人回答后返回

### InterviewFlow

面试状态机主控类：

```python
class InterviewFlow:
    def __init__(
        self,
        questions: List[Dict[str, Any]],      # 题库列表
        judge: InterviewJudge,                # 评判器实例
        max_followups_per_question: int = 2,  # 单题最多追问次数
        global_turn_limit: int = 20,          # 全局最大轮次
    )
```

**核心方法**：

#### `async produce_interviewer_message() -> FlowResponse`

生成面试官消息，根据当前状态返回不同内容：

- `INTRO`：欢迎语，转换到 `ASK_QUESTION`
- `ASK_QUESTION`：当前主问题，转换到 `WAIT_ANSWER`
- `ASK_FOLLOWUP`：追问内容，转换到 `WAIT_ANSWER`
- `WRAP_UP`：结束语，转换到 `DONE`
- `DONE`：返回空消息

**调用时机**：
1. 面试开始时生成 INTRO 和第一个问题
2. Judge 决策完成后，根据新状态生成下一步消息

#### `async receive_candidate_answer(answer: str) -> FlowResponse`

接收候选人回答并推进状态机：

**处理流程**：
```
1. 验证当前状态必须是 WAIT_ANSWER
2. 记录回答到 QuestionContext.turns
3. 增加全局轮次计数
4. 检查全局轮次限制（Guard 1）
   - 如达到上限，强制返回 move_forward=True 的 Decision
5. 调用 Judge.decide() 评估回答（LLM 调用）
6. 根据 Decision 决定状态转换：
   - need_follow_up=True → ASK_FOLLOWUP
   - move_forward=True → ASK_QUESTION（或 WRAP_UP）
7. 返回 FlowResponse，包含决策结果和转换路径
```

**异常处理**：
- 如果 `state != WAIT_ANSWER`，抛出 `RuntimeError`

## 配置项详解

### 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
| `questions` | `List[Dict]` | 必填 | 题库列表，每个元素包含 `question_id`、`main_question`、`evidence` |
| `judge` | `InterviewJudge` | 必填 | 评判器实例，负责回答质量评估 |
| `max_followups_per_question` | `int` | 2 | 单个问题最多追问次数，超出后强制下一题 |
| `global_turn_limit` | `int` | 20 | 全局最大轮次（候选人发言次数），超出强制结束 |

### 题库格式

每个问题必须包含以下字段：

```python
{
    "question_id": "q1",                  # 必填：唯一标识
    "main_question": "请介绍你的项目经验",  # 必填：问题文本
    "evidence": {                         # 可选：评分依据
        "scoring_boundary": "是否体现目标、动作、结果"
    }
}
```

## 数据流向

### 面试启动阶段

```
service._interview_handler_loop()
  ├─> flow.produce_interviewer_message()  [INTRO -> ASK_QUESTION]
  ├─> TTS 播放开场白
  ├─> flow.produce_interviewer_message()  [ASK_QUESTION -> WAIT_ANSWER]
  └─> TTS 播放第一个问题
```

### 候选人回答处理

```
候选人语音 (WebSocket)
  ├─> ASR 识别文本
  ├─> flow.receive_candidate_answer(text)
  │     ├─> WAIT_ANSWER -> EVAL_ANSWER -> DECIDE
  │     ├─> judge.decide() 调用 LLM 评估
  │     └─> 根据 Decision 决定下一步状态
  ├─> flow.produce_interviewer_message()  [生成面试官回应]
  └─> Interviewer LLM + TTS 生成自然语言回应
```

### 完整轮次流程

```
┌──────────────┐
│ ASK_QUESTION │ ← 主问题
└──────┬───────┘
       ↓
┌──────────────┐
│ WAIT_ANSWER  │ ← 等待候选人
└──────┬───────┘
       ↓
┌──────────────┐
│ EVAL_ANSWER  │ ← 调用 Judge（瞬态）
└──────┬───────┘
       ↓
┌──────────────┐
│   DECIDE     │ ← 决策分支（瞬态）
└──────┬───────┘
       ├─→ ASK_FOLLOWUP  [回答不足 + 未达追问上限]
       ├─→ ASK_QUESTION  [回答充分 + 还有下一题]
       └─→ WRAP_UP       [最后一题完成 / 全局限制]
```

## 使用示例

### 基本初始化

```python
from interview_flow import InterviewFlow
from interview_judge import InterviewJudge

# 准备题库
questions = [
    {
        "question_id": "q1",
        "main_question": "请介绍一个你主导的项目",
        "evidence": {
            "scoring_boundary": "是否包含项目目标、个人动作、可量化结果"
        }
    },
    {
        "question_id": "q2",
        "main_question": "描述一次你解决技术难题的经历",
        "evidence": {
            "scoring_boundary": "是否体现问题拆解、方案选型、落地验证"
        }
    }
]

# 创建 Judge
judge = InterviewJudge(
    llm_endpoint_id="ep-xxx",
    coverage_threshold=0.7,
    max_followups_per_question=1
)

# 创建 Flow
flow = InterviewFlow(
    questions=questions,
    judge=judge,
    max_followups_per_question=1,
    global_turn_limit=15
)
```

### 开场流程

```python
# 生成开场白
intro_resp = await flow.produce_interviewer_message()
print(intro_resp.interviewer_text)
# 输出："你好，欢迎参加面试。本场共2道题，我会根据你的回答决定是否追问。"

# 生成第一个问题
q1_resp = await flow.produce_interviewer_message()
print(q1_resp.interviewer_text)
# 输出："请介绍一个你主导的项目"
print(q1_resp.state_after)  # WAIT_ANSWER
```

### 处理候选人回答

```python
# 接收回答
answer = "我在2023年负责了一个电商推荐系统..."
resp = await flow.receive_candidate_answer(answer)

# 检查决策
if resp.decision:
    print(f"覆盖度: {resp.decision.coverage_score:.2f}")
    print(f"是否追问: {resp.decision.need_follow_up}")
    print(f"理由: {resp.decision.reason}")

    if resp.decision.need_follow_up:
        print(f"追问: {resp.decision.follow_up_question}")

# 生成面试官回应
next_resp = await flow.produce_interviewer_message()
print(next_resp.interviewer_text)
```

### 检查完成状态

```python
# 检查是否结束
if flow.is_done:
    print("面试已完成")
    print(f"总轮次: {flow.total_candidate_turns}")
    print(f"完成题数: {flow.current_question_index + 1}")
```

## 与其他模块协作

### 与 InterviewJudge 的交互

```python
# Flow 在 receive_candidate_answer() 中调用 Judge
decision = await self.judge.decide(
    question=q.main_question,
    candidate_answer=answer,
    follow_up_count=q.follow_up_count,
    evidence=q.evidence,
)
```

**Judge 返回的 Decision 字段**：
- `move_forward`: 是否进入下一题
- `need_follow_up`: 是否需要追问
- `follow_up_question`: 追问内容（如需追问）
- `reason`: 决策理由（用于日志）
- `coverage_score`: 覆盖度得分

### 与 VoiceBotService 的集成

Service 的 `_interview_handler_loop()` 编排完整流程：

```python
# 1. 发送开场和第一题（scripted TTS，无 LLM）
intro_resp = await flow.produce_interviewer_message()
await send_tts(intro_resp.interviewer_text)

# 2. 主循环
while True:
    # 等待候选人回答
    asr_recognized = await get_candidate_answer()

    # 处理回答（触发 Judge LLM）
    answer_resp = await flow.receive_candidate_answer(asr_recognized.sentence)

    # 如果结束则退出
    if flow.is_done:
        break

    # 生成下一步内容（主问题或追问）
    next_resp = await flow.produce_interviewer_message()

    # 调用 Interviewer LLM 生成自然语言
    interviewer_text = await stream_interview_llm_chat(
        decision=answer_resp.decision,
        next_question=next_resp.interviewer_text,
        flow_state=flow.state
    )
```

## 状态一致性保证

### 状态转换原子性

每个方法调用完成一次完整的状态转换，不存在中间状态：

```python
# ✅ 正确：一次调用完成完整转换
resp = await flow.produce_interviewer_message()
# 状态已从 INTRO 变为 ASK_QUESTION

# ❌ 错误：外部不应修改内部状态
flow.state = ASK_QUESTION  # 这会破坏状态机一致性
```

### 调用顺序约束

**必须严格遵守的调用模式**：

```python
# 模式 1：开场流程
await produce_interviewer_message()  # INTRO -> ASK_QUESTION
await produce_interviewer_message()  # ASK_QUESTION -> WAIT_ANSWER

# 模式 2：回答-响应循环
await receive_candidate_answer(text)  # WAIT_ANSWER -> DECIDE -> (新状态)
await produce_interviewer_message()   # 根据新状态生成消息
```

**禁止的调用模式**：

```python
# ❌ 连续调用 produce_interviewer_message() 在 WAIT_ANSWER 状态
# 会抛出 RuntimeError: state WAIT_ANSWER does not produce interviewer message

# ❌ 在非 WAIT_ANSWER 状态调用 receive_candidate_answer()
# 会抛出 RuntimeError: expected WAIT_ANSWER, got ASK_QUESTION
```

## 日志与排障

### 关键日志字段

Flow 不直接打印日志，但通过 FlowResponse 提供调试信息：

```python
resp = await flow.receive_candidate_answer(answer)
# 日志应包含：
# - state_before / state_after
# - transition_trace（转换路径）
# - decision（Judge 决策结果）
# - question_id（当前问题）
```

### Service 层日志示例

```python
self._log(
    f"[Interview] Judge result: "
    f"{answer_resp.state_before}->{answer_resp.state_after} "
    f"q={answer_resp.question_id} "
    f"move_forward={decision.move_forward} "
    f"need_follow_up={decision.need_follow_up} "
    f"coverage={decision.coverage_score:.2f} "
    f"reason={decision.reason}"
)
```

### 常见故障排查

#### 1. 面试卡住不推进

**症状**：状态一直停留在 WAIT_ANSWER

**可能原因**：
- Service 层未调用 `receive_candidate_answer()`
- ASR 识别失败，未触发回答处理

**排查步骤**：
```bash
# 检查面试日志中是否有 "Candidate answer:" 记录
grep "Candidate answer" backend/data/storage/interview_logs/<token>/backend.log

# 检查状态转换记录
grep "Judge result" backend/data/storage/interview_logs/<token>/backend.log
```

#### 2. 追问次数超出预期

**症状**：某个问题被追问了 3 次或更多

**可能原因**：
- `max_followups_per_question` 配置错误
- Judge 一直返回 `need_follow_up=True`

**排查步骤**：
```python
# 检查 Flow 初始化参数
print(flow.max_followups_per_question)  # 应该是 1 或 2

# 检查 Judge 配置
print(judge.max_followups_per_question)  # 应该与 Flow 一致
```

#### 3. 面试提前结束

**症状**：题库有 5 题但只问了 2 题

**可能原因**：
- 触发了 `global_turn_limit`
- 某个 Decision 的 `move_forward=True` 且题库用完

**排查步骤**：
```python
# 检查结束时的状态
print(f"Total turns: {flow.total_candidate_turns}")
print(f"Limit: {flow.global_turn_limit}")
print(f"Completed questions: {flow.current_question_index + 1}")

# 查看最后一次决策
grep "global_turn_limit_reached" backend/data/storage/interview_logs/<token>/backend.log
```

#### 4. 状态机抛出异常

**症状**：`RuntimeError: expected WAIT_ANSWER, got ...`

**原因**：调用顺序错误，违反了状态转换约束

**修复**：
```python
# 检查调用链，确保遵循：
# produce -> produce (开场) -> receive -> produce -> receive -> ...
```

## 扩展与定制

### 自定义强制结束条件

可以在 `receive_candidate_answer()` 中增加自定义 Guard：

```python
# 示例：连续 3 次低分强制结束
if q.coverage_score < 0.3 and self.consecutive_low_scores >= 2:
    forced = Decision(
        move_forward=True,
        need_follow_up=False,
        follow_up_question="",
        reason="consecutive_low_scores",
        coverage_score=q.coverage_score,
    )
    self.state = WRAP_UP
    return FlowResponse(...)
```

### 增加新状态

如需增加 `ASK_CLARIFICATION`（澄清）状态：

1. 定义状态常量：
```python
ASK_CLARIFICATION = "ASK_CLARIFICATION"
```

2. 更新转换逻辑：
```python
# 在 DECIDE 中增加分支
if decision.need_clarification:
    self.state = ASK_CLARIFICATION
    return FlowResponse(...)
```

3. 在 `produce_interviewer_message()` 中处理：
```python
if self.state == ASK_CLARIFICATION:
    self.state = WAIT_ANSWER
    return FlowResponse(interviewer_text=clarification_text, ...)
```

## 性能考虑

### LLM 调用开销

每次调用 `receive_candidate_answer()` 都会触发一次 Judge LLM 调用：

```python
# 假设题库 5 题，每题追问 1 次
# 最坏情况：5 * (1 + 1) = 10 次 Judge LLM 调用
# 加上每轮的 Interviewer LLM，总共约 20 次 LLM 调用
```

**优化建议**：
- 使用 `llm_limiter` 控制并发
- 对于简单场景，启用 Judge 的 `_heuristic_decision_json()` 启发式模式

### 内存占用

每个 `QuestionContext.turns` 会记录完整对话：

```python
# 20 轮对话，每轮平均 200 字
# 约 20 * 200 = 4000 字符 ≈ 8KB
# 对于高并发场景（100 场面试）约 800KB，可忽略
```

## 相关测试

### 单元测试覆盖

```bash
# 测试状态转换
pytest backend/tests/test_interview_flow_states.py

# 测试追问逻辑
pytest backend/tests/test_interview_flow_followup.py

# 测试全局限制
pytest backend/tests/test_interview_flow_limits.py
```

### 集成测试

```bash
# 完整面试流程模拟
pytest backend/tests/test_interview_flow_integration.py
```
