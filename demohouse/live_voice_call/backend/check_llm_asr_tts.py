#!/usr/bin/env python3
"""
同时检测 LLM、ASR、TTS 是否可用。

环境变量（与 handler 启动保持一致）：
  ARK_API_KEY          - 火山方舟 API Key（必填）
  volcengine-python-sdk - 需为 5.0.19（用于 responses API）
  LLM1_ENDPOINT_ID     - 方舟 LLM#1 endpoint ID（Judge）
  LLM2_ENDPOINT_ID     - 方舟 LLM#2 endpoint ID（Interviewer）
  LLM1_THINKING_TYPE   - enabled|disabled|auto
  LLM2_THINKING_TYPE   - enabled|disabled|auto
  LLM1_REASONING_EFFORT - minimal|low|medium|high（仅 thinking=enabled 生效）
  LLM2_REASONING_EFFORT - minimal|low|medium|high（仅 thinking=enabled 生效）
  ASR_APP_ID           - ASR 应用 ID
  ASR_ACCESS_TOKEN     - ASR Access Token
  ASR_RESOURCE_ID      - ASR 资源 ID（例如 volc.bigasr.sauc.duration）
  ASR_WS_URL           - ASR WebSocket 地址（可选，默认 bigmodel_async）
  TTS_APP_ID           - TTS 应用 ID
  TTS_ACCESS_TOKEN     - TTS Access Token
"""
import asyncio
import sys

from startup_self_check import format_self_check_lines, run_startup_self_check


async def main():
    print("检测 LLM / ASR / TTS ...")
    report = await run_startup_self_check()
    for line in format_self_check_lines(report):
        print(line)
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
