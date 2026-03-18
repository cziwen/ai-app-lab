# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# Licensed under the 【火山方舟】原型应用软件自用许可协议
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     https://www.volcengine.com/docs/82379/1433703
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import AsyncIterable
from urllib.parse import parse_qs, urlparse

import uvicorn
import websockets

from arkitect.telemetry.logger import INFO
from arkitect.utils.binary_protocol import parse_request
from arkitect.utils.event_loop import get_event_loop
from admin_api import create_admin_app
from admin_store import (
    ensure_default_admin,
    mark_interview_completed,
    persist_interview_audio,
    save_interview_turns,
    start_interview_session,
    update_interview_updated_at,
)
from service import VoiceBotService
from startup_self_check import (
    format_self_check_lines,
    load_runtime_config,
    run_startup_self_check,
)
from utils import *

RUNTIME_CONFIG = load_runtime_config()
ensure_default_admin()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Set up file logging
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Backend file logger — attached to "root" named logger to capture arkitect INFO() calls
backend_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "backend.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
backend_file_handler.setLevel(logging.INFO)
backend_file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
logging.getLogger("root").addHandler(backend_file_handler)

# Frontend file logger — separate logger for frontend logs received via HTTP
frontend_logger = logging.getLogger("frontend")
frontend_logger.setLevel(logging.INFO)
frontend_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "frontend.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
frontend_file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(message)s")
)
frontend_logger.addHandler(frontend_file_handler)
frontend_logger.propagate = False

ADMIN_API_HOST = os.getenv("ADMIN_API_HOST", "127.0.0.1")
ADMIN_API_PORT = int(os.getenv("ADMIN_API_PORT", "8890"))


def _extract_pcm_audio(raw_audio: bytes) -> bytes:
    """Extract raw PCM from the nested audio protocol if present."""
    if not raw_audio:
        return b""
    try:
        parsed = parse_request(raw_audio)
        if isinstance(parsed, (bytes, bytearray)):
            return bytes(parsed)
    except Exception:
        pass
    return raw_audio


async def handler(websocket: websockets.WebSocketCommonProtocol, path):
    """
    Asynchronous function to handle WebSocket connections.

    Args:
        websocket (websockets.WebSocketCommonProtocol): The client's WebSocket connection.
        path (str): The requested path.
    """
    parsed_path = urlparse(path or "")
    token = (parse_qs(parsed_path.query).get("token", [None])[0] or "").strip()
    interview_data = start_interview_session(token) if token else None
    if not interview_data:
        invalid_payload = BotErrorPayload(
            error=ErrorEvent(code="INVALID_TOKEN", message="面试链接无效或已失效")
        )
        await websocket.send(
            convert_web_event_to_binary(WebEvent.from_payload(invalid_payload))
        )
        await websocket.close()
        return

    turns = []
    candidate_audio = bytearray()
    interviewer_audio_raw = bytearray()
    interviewer_audio_pcm = bytearray()
    interview_completed = False

    def record_turn(role: str, text: str):
        if not text:
            return
        turns.append((role, text, datetime.now(timezone.utc).isoformat()))

    def on_interview_completed():
        nonlocal interview_completed
        interview_completed = True

    def record_bot_audio(chunk: bytes):
        interviewer_audio_raw.extend(chunk)
        interviewer_audio_pcm.extend(chunk)

    # Create a VoiceBotService instance and initialize it
    service = VoiceBotService(
        llm_ep_id=RUNTIME_CONFIG.llm_endpoint_id,
        tts_app_key=RUNTIME_CONFIG.tts_app_id,
        tts_access_key=RUNTIME_CONFIG.tts_access_token,
        asr_app_key=RUNTIME_CONFIG.asr_app_id,
        asr_access_key=RUNTIME_CONFIG.asr_access_token,
        interview_mode=True,
        interview_questions=interview_data.questions,
        on_candidate_sentence=lambda text: record_turn("candidate", text),
        on_bot_sentence=lambda text: record_turn("interviewer", text),
        on_bot_audio_chunk=record_bot_audio,
        on_interview_completed=on_interview_completed,
    )
    await service.init()
    # Send a bot ready message
    await websocket.send(
        convert_web_event_to_binary(
            WebEvent.from_payload(BotReadyPayload(session=str(uuid.uuid4())))
        )
    )

    async def async_gen(
        ws: websockets.WebSocketCommonProtocol,
    ) -> AsyncIterable[WebEvent]:
        """
        Asynchronously generate input events from the WebSocket connection.

        Args:
            ws (websockets.WebSocketCommonProtocol): The client's WebSocket connection.

        Returns:
            AsyncIterable[WebEvent]: An asynchronous generator of input events.
        """
        async for m in ws:
            input_event = convert_binary_to_web_event_to_binary(m)
            data_len = len(input_event.data) if input_event.data else 0
            INFO(
                f"Received input event: {input_event.event}, \
                payload: {input_event.event}, data len:{data_len}"
            )
            if input_event.event == USER_AUDIO and input_event.data:
                pcm_bytes = _extract_pcm_audio(input_event.data)
                if pcm_bytes:
                    candidate_audio.extend(pcm_bytes)
            yield input_event

    async def fetch_output(
        ws: websockets.WebSocketCommonProtocol, output_events: AsyncIterable[WebEvent]
    ) -> None:
        """
        Asynchronously fetch and send output events to the WebSocket connection.

        Args:
            ws (websockets.WebSocketCommonProtocol): The client's WebSocket connection.
            output_events (AsyncIterable[WebEvent]): An asynchronous generator of output events.
        """
        async for output_event in output_events:
            INFO(
                f"Sending output event= {output_event.event}, \
                data len:{len(output_event.data) if output_event.data else 0} , payload: {output_event.payload}"
            )
            await ws.send(convert_web_event_to_binary(output_event))

    INFO(f"New connection: {websocket.remote_address}")
    try:
        # Start the handler loop and asynchronously fetch output events
        outputs = service.handler_loop(async_gen(websocket))
        await asyncio.create_task(fetch_output(websocket, outputs))
    except websockets.exceptions.ConnectionClosed as e:
        INFO(f"Connection closed: {e}")
    finally:
        try:
            save_interview_turns(token, turns)
            persist_interview_audio(
                token=token,
                candidate_pcm_bytes=bytes(candidate_audio),
                interviewer_pcm_bytes=bytes(interviewer_audio_pcm),
                interviewer_raw_bytes=bytes(interviewer_audio_raw),
            )
            if interview_completed:
                mark_interview_completed(token)
            else:
                update_interview_updated_at(token)
        except Exception as persist_err:
            INFO(f"[InterviewPersist] failed token={token} error={persist_err}")


async def handle_frontend_log_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
):
    """Minimal HTTP handler for frontend log ingestion."""
    request_line = await reader.readline()
    headers = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        key, _, value = line.decode().partition(":")
        headers[key.strip().lower()] = value.strip()

    method, path, _ = request_line.decode().split(" ", 2)

    if method == "POST" and path == "/api/frontend-logs":
        content_length = int(headers.get("content-length", 0))
        body = await reader.readexactly(content_length)
        try:
            log_entries = json.loads(body)
            if isinstance(log_entries, list):
                for entry in log_entries:
                    frontend_logger.info(str(entry))
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Length: 2\r\n"
                b"Access-Control-Allow-Origin: *\r\n"
                b"\r\nOK"
            )
        except (json.JSONDecodeError, Exception):
            response = (
                b"HTTP/1.1 400 Bad Request\r\n"
                b"Content-Length: 11\r\n"
                b"Access-Control-Allow-Origin: *\r\n"
                b"\r\nBad Request"
            )
    elif method == "OPTIONS":
        response = (
            b"HTTP/1.1 204 No Content\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"Access-Control-Allow-Methods: POST, OPTIONS\r\n"
            b"Access-Control-Allow-Headers: Content-Type\r\n"
            b"Access-Control-Max-Age: 86400\r\n"
            b"\r\n"
        )
    else:
        response = (
            b"HTTP/1.1 404 Not Found\r\n"
            b"Content-Length: 9\r\n"
            b"\r\nNot Found"
        )

    writer.write(response)
    await writer.drain()
    writer.close()


async def main():
    """
    Main function to start the WebSocket server and HTTP log server.
    """
    # Clear log files on each startup so logs are fresh per dev session
    for log_file in ("backend.log", "frontend.log"):
        log_path = os.path.join(LOG_DIR, log_file)
        if os.path.exists(log_path):
            open(log_path, "w").close()

    INFO("[StartupSelfCheck] running preflight checks")
    self_check_report = await run_startup_self_check(RUNTIME_CONFIG)
    for line in format_self_check_lines(self_check_report):
        INFO(line)
    if not self_check_report.ok:
        INFO("[StartupSelfCheck] failed, aborting server startup")
        raise SystemExit(1)

    # Start the WebSocket server listening on 127.0.0.1:8888
    ws_server = await websockets.serve(handler, host="127.0.0.1", port=8888)
    INFO("WebSocket server is running on ws://127.0.0.1:8888")

    # Start the HTTP log server on port 8889
    http_server = await asyncio.start_server(
        handle_frontend_log_request, host="127.0.0.1", port=8889
    )
    INFO("HTTP log server is running on http://127.0.0.1:8889")

    admin_app = create_admin_app()
    admin_config = uvicorn.Config(
        app=admin_app,
        host=ADMIN_API_HOST,
        port=ADMIN_API_PORT,
        log_level="info",
        loop="asyncio",
    )
    admin_server = uvicorn.Server(admin_config)
    INFO(f"Admin API server is running on http://{ADMIN_API_HOST}:{ADMIN_API_PORT}")

    await asyncio.gather(
        ws_server.wait_closed(),
        http_server.serve_forever(),
        admin_server.serve(),
    )


if __name__ == "__main__":
    get_event_loop(main())
