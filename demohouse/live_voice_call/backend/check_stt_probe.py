#!/usr/bin/env python3
"""
Manual STT probe script (submit + query) for AUC bigmodel.

Usage examples:
  python backend/check_stt_probe.py --url https://smartinterview.cn/stt_probe.wav
  python backend/check_stt_probe.py --url https://xx/sample.mp3 --format mp3

Environment variables:
  STT_APP_ID
  STT_ACCESS_TOKEN
  STT_RESOURCE_ID
  STT_SUBMIT_URL                (optional)
  STT_QUERY_URL                 (optional)
  STT_TASK_TIMEOUT_MS           (optional, default 90000)
  STT_POLL_INTERVAL_MS          (optional, default 1000)
  STT_SELF_CHECK_AUDIO_URL      (optional fallback for --url)
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from auc_stt_client import AucSTTClient, AucSTTError, AucSTTTimeoutError


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _infer_format_from_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    suffix = Path(parsed.path).suffix.lower()
    if suffix == ".wav":
        return "wav"
    if suffix == ".mp3":
        return "mp3"
    if suffix == ".ogg":
        return "ogg"
    return "raw"


def _to_int(raw: str, default: int, minimum: int) -> int:
    try:
        parsed = int(str(raw or "").strip())
    except ValueError:
        parsed = default
    return max(minimum, parsed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual STT probe (submit + query).")
    parser.add_argument(
        "--url",
        default="",
        help="Public audio URL. Fallback: STT_SELF_CHECK_AUDIO_URL",
    )
    parser.add_argument(
        "--format",
        default="",
        choices=["wav", "mp3", "ogg", "raw", ""],
        help="Audio format override. Default: infer from URL path suffix",
    )
    parser.add_argument(
        "--language",
        default="",
        help="Optional language, e.g. zh-CN, en-US",
    )
    args = parser.parse_args()

    app_id = _env("STT_APP_ID")
    access_token = _env("STT_ACCESS_TOKEN")
    resource_id = _env("STT_RESOURCE_ID")
    submit_url = _env("STT_SUBMIT_URL")
    query_url = _env("STT_QUERY_URL")
    timeout_ms = _to_int(_env("STT_TASK_TIMEOUT_MS"), default=90000, minimum=5000)
    poll_ms = _to_int(_env("STT_POLL_INTERVAL_MS"), default=1000, minimum=200)

    missing = []
    if not app_id:
        missing.append("STT_APP_ID")
    if not access_token:
        missing.append("STT_ACCESS_TOKEN")
    if not resource_id:
        missing.append("STT_RESOURCE_ID")
    if missing:
        print(f"[STTProbe] missing required env: {','.join(missing)}")
        return 2

    url = str(args.url or "").strip() or _env("STT_SELF_CHECK_AUDIO_URL")
    if not url:
        print("[STTProbe] missing probe URL: use --url or STT_SELF_CHECK_AUDIO_URL")
        return 2

    audio_format = str(args.format or "").strip().lower() or _infer_format_from_url(url)

    print(
        "[STTProbe] start "
        f"url={url} format={audio_format} resource_id={resource_id} "
        f"timeout_ms={timeout_ms} poll_ms={poll_ms}"
    )

    client = AucSTTClient(
        app_id=app_id,
        access_token=access_token,
        resource_id=resource_id,
        submit_url=submit_url,
        query_url=query_url,
        task_timeout_ms=timeout_ms,
        poll_interval_ms=poll_ms,
        log_fn=lambda msg: print(msg),
    )

    try:
        result = client.transcribe_audio_url(
            audio_url=url,
            audio_format=audio_format,
            language=str(args.language or "").strip(),
            show_utterances=True,
        )
    except AucSTTTimeoutError as err:
        print(f"[STTProbe] FAIL timeout error={err}")
        return 1
    except AucSTTError as err:
        print(f"[STTProbe] FAIL api error={err}")
        return 1
    except Exception as err:
        print(f"[STTProbe] FAIL unexpected error={type(err).__name__}: {err}")
        return 1

    text = str(result.text or "").strip()
    utter_count = len(result.utterances or [])
    print(
        "[STTProbe] PASS "
        f"task_id={result.task_id} text_len={len(text)} utterances={utter_count}"
    )
    if text:
        preview = text[:200].replace("\n", " ")
        print(f"[STTProbe] text_preview={preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
