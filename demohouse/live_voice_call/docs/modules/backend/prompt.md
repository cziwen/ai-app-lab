# backend/prompt.py

## 模块概述
`prompt.py` 维护系统提示词模板，包含：
- 通用语音助手提示词：`SYSTEM_PROMPT`
- 面试官提示词：`INTERVIEWER_SYSTEM_PROMPT`

## 面试官提示词关键约束
- 提问态（主问题/子问题/追问/澄清）只能输出问句。
- 必须完整表达 `[下一步内容]`，优先题干信息保真。
- 默认禁止追加题干外句子（过渡语、总结语、引导语）。
- 末尾必须以问号结尾。
- 澄清场景仅解释题意，不新增考察点。

## 对外类
- `VoiceBotPrompt`
- `InterviewerPrompt`

## 相关测试
- `pytest backend/tests/test_service_interview_llm.py`
- `pytest backend/tests/test_prompt_quality_live_api.py`
