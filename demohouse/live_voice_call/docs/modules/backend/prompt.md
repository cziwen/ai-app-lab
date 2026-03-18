# backend/prompt.py

## 模块职责
- 负责 `prompt` 相关逻辑（基于文件命名与代码结构）。

## 入口与调用方
- 被后端主流程或相关模块导入调用。
- 主要入口文件参考：`backend/handler.py`、`backend/admin_api.py`。

## 对外接口（函数/类）
L83: class VoiceBotPrompt(BaseChatPromptTemplate):
L125: class InterviewerPrompt(BaseChatPromptTemplate):

## 依赖与配置
- 关键导入（节选）：
L12: from typing import Any, List
L14: from langchain.prompts.chat import BaseChatPromptTemplate
L15: from langchain_core.messages import AnyMessage, BaseMessage, SystemMessage

## 日志与排障
- 优先检查后端进程日志与每场面试日志（`backend/logs`）。
- 若涉及数据状态，结合 `backend/data/storage` 中 SQLite 与音频落盘结果排查。

## 常见故障与排查步骤
1. 启动失败：先检查环境变量与 `startup_self_check` 输出。
2. 行为异常：定位到本模块接口，确认上下游调用参数与返回值。
3. 数据不一致：核对 SQLite 记录、日志时间线、音频文件是否同步落盘。

## 相关测试
- 查看 `backend/tests` 下与模块同名或同领域测试用例进行回归验证。
