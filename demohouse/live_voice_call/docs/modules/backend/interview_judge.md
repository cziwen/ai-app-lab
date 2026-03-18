# backend/interview_judge.py

## 模块职责
- 负责 `interview_judge` 相关逻辑（基于文件命名与代码结构）。

## 入口与调用方
- 被后端主流程或相关模块导入调用。
- 主要入口文件参考：`backend/handler.py`、`backend/admin_api.py`。

## 对外接口（函数/类）
L14: class Decision:
L22: class JudgePrompt(BaseChatPromptTemplate):
L39: class InterviewJudge:

## 依赖与配置
- 关键导入（节选）：
L1: import json
L2: from dataclasses import dataclass
L3: from typing import Any, Awaitable, Callable, Dict, List, Optional
L5: from arkitect.core.component.llm import BaseChatLanguageModel
L6: from arkitect.core.component.llm.model import ArkMessage
L7: from langchain.prompts.chat import BaseChatPromptTemplate
L8: from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage, SystemMessage
L10: from llm_limiter import llm_slot

## 日志与排障
- 优先检查后端进程日志与每场面试日志（`backend/logs`）。
- 若涉及数据状态，结合 `backend/data/storage` 中 SQLite 与音频落盘结果排查。

## 常见故障与排查步骤
1. 启动失败：先检查环境变量与 `startup_self_check` 输出。
2. 行为异常：定位到本模块接口，确认上下游调用参数与返回值。
3. 数据不一致：核对 SQLite 记录、日志时间线、音频文件是否同步落盘。

## 相关测试
- 查看 `backend/tests` 下与模块同名或同领域测试用例进行回归验证。
