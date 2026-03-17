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
import os
import sys

# 默认与 handler.py 保持一致
DEFAULT_LLM_ENDPOINT_ID = "ep-m-20260315140910-pfztd"
DEFAULT_ASR_APP_ID = "2057385740"
DEFAULT_ASR_ACCESS_TOKEN = "bnO29ab2sIHtKyt3f-Dn8SAYaMZr04BP"
DEFAULT_TTS_APP_ID = "2057385740"
DEFAULT_TTS_ACCESS_TOKEN = "bnO29ab2sIHtKyt3f-Dn8SAYaMZr04BP"
DEFAULT_TTS_SPEAKER = "zh_female_sajiaonvyou_moon_bigtts"


def _env(key: str, default: str) -> str:
    return os.environ.get(key) or default


async def check_llm() -> bool:
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        print("  [LLM] 跳过: 未设置 ARK_API_KEY")
        return False
    endpoint_id = _env("LLM_ENDPOINT_ID", DEFAULT_LLM_ENDPOINT_ID)
    from arkitect.core.component.llm import BaseChatLanguageModel
    from arkitect.core.component.llm.model import ArkMessage
    from prompt import VoiceBotPrompt

    messages = [ArkMessage(**{"role": "user", "content": "你好，回复一句话即可。"})]
    llm = BaseChatLanguageModel(
        template=VoiceBotPrompt(),
        messages=messages,
        endpoint_id=endpoint_id,
    )
    try:
        first = None
        async for chunk in llm.astream():
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                c = chunk.choices[0].delta.content
                if first is None:
                    first = c
                print(c, end="", flush=True)
        print()
        if first is not None:
            print("  [LLM] ✓ 可用")
            return True
        print("  [LLM] ⚠ 未收到内容")
        return False
    except Exception as e:
        print(f"  [LLM] ✗ 失败: {e}")
        return False


async def check_asr() -> bool:
    asr_app_id = _env("ASR_APP_ID", DEFAULT_ASR_APP_ID)
    asr_token = _env("ASR_ACCESS_TOKEN", DEFAULT_ASR_ACCESS_TOKEN)
    from arkitect.core.component.asr import AsyncASRClient

    client = AsyncASRClient(app_key=asr_app_id, access_key=asr_token)
    try:
        await client.init()
        # 用空音频流做一次短检测，仅验证连接与鉴权
        async def empty_audio():
            yield b""

        first = None
        async for rsp in client.stream_asr(empty_audio()):
            first = rsp
            break
        await client.close()
        print("  [ASR] ✓ 可用")
        return True
    except Exception as e:
        print(f"  [ASR] ✗ 失败: {e}")
        return False


async def check_tts() -> bool:
    tts_app_id = _env("TTS_APP_ID", DEFAULT_TTS_APP_ID)
    tts_token = _env("TTS_ACCESS_TOKEN", DEFAULT_TTS_ACCESS_TOKEN)
    speaker = _env("TTS_SPEAKER", DEFAULT_TTS_SPEAKER)
    from arkitect.core.component.tts import AsyncTTSClient, AudioParams, ConnectionParams
    from arkitect.core.component.tts.constants import EventSessionFinished

    client = AsyncTTSClient(
        app_key=tts_app_id,
        access_key=tts_token,
        connection_params=ConnectionParams(
            speaker=speaker, audio_params=AudioParams()
        ),
    )
    try:
        await client.init()

        async def one_sentence():
            yield "你好"

        got_audio = False
        async for rsp in client.tts(source=one_sentence(), include_transcript=True):
            if rsp.audio:
                got_audio = True
            if rsp.event == EventSessionFinished:
                break
        await client.close()
        if got_audio:
            print("  [TTS] ✓ 可用")
            return True
        print("  [TTS] ⚠ 未收到音频")
        return False
    except Exception as e:
        print(f"  [TTS] ✗ 失败: {e}")
        return False


async def main():
    print("检测 LLM / ASR / TTS ...\n")

    print("[1] LLM:")
    ok_llm = await check_llm()

    print("\n[2] ASR:")
    ok_asr = await check_asr()

    print("\n[3] TTS:")
    ok_tts = await check_tts()

    print()
    if ok_llm and ok_asr and ok_tts:
        print("全部可用 ✓")
        sys.exit(0)
    failed = []
    if not ok_llm:
        failed.append("LLM")
    if not ok_asr:
        failed.append("ASR")
    if not ok_tts:
        failed.append("TTS")
    print(f"未通过: {', '.join(failed)}")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
