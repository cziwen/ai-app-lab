#!/usr/bin/env python3
"""
同时检测 LLM、ASR、TTS 是否可用。

环境变量（均可选覆盖 handler 默认值）：
  ARK_API_KEY          - 火山方舟 API Key（LLM 必填）
  LLM_ENDPOINT_ID      - 方舟 LLM endpoint ID
  ASR_APP_ID           - ASR 应用 ID
  ASR_ACCESS_TOKEN     - ASR Access Token
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
