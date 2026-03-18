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
from typing import AsyncIterable, Callable, Dict, Tuple
from urllib.parse import parse_qs, urlparse

import uvicorn
import websockets

from arkitect.utils.binary_protocol import parse_request
from arkitect.utils.event_loop import get_event_loop
from admin_api import create_admin_app
from admin_store import (
    INTERVIEW_LOG_DIR,
    ensure_default_admin,
    interview_exists,
    mark_interview_disconnected,
    mark_interview_completed,
    persist_interview_audio,
    save_interview_turns,
    start_interview_session,
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

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(INTERVIEW_LOG_DIR, exist_ok=True)

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
SERVER_LOG_PATH = os.path.join(LOG_DIR, "backend.log")

_INTERVIEW_LOGGER_CACHE: Dict[Tuple[str, str], logging.Logger] = {}


def _build_file_handler(log_path: str, log_format: str) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(log_format))
    return handler


server_logger = logging.getLogger("server")
server_logger.setLevel(logging.INFO)
server_logger.propagate = False
if not any(
    isinstance(handler, RotatingFileHandler)
    and os.path.abspath(getattr(handler, "baseFilename", "")) == os.path.abspath(SERVER_LOG_PATH)
    for handler in server_logger.handlers
):
    server_logger.addHandler(
        _build_file_handler(
            SERVER_LOG_PATH,
            "%(asctime)s - %(levelname)s - %(message)s",
        )
    )


def _get_interview_logger(token: str, stream: str) -> logging.Logger:
    if stream not in ("backend", "frontend"):
        raise ValueError(f"unsupported stream: {stream}")

    cache_key = (token, stream)
    cached = _INTERVIEW_LOGGER_CACHE.get(cache_key)
    if cached:
        return cached

    interview_dir = os.path.join(str(INTERVIEW_LOG_DIR), token)
    os.makedirs(interview_dir, exist_ok=True)

    log_path = os.path.join(interview_dir, f"{stream}.log")
    logger = logging.getLogger(f"interview.{stream}.{token}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler_exists = any(
        isinstance(handler, RotatingFileHandler)
        and os.path.abspath(getattr(handler, "baseFilename", "")) == os.path.abspath(log_path)
        for handler in logger.handlers
    )
    if not handler_exists:
        fmt = (
            "%(asctime)s - %(levelname)s - %(message)s"
            if stream == "backend"
            else "%(asctime)s - %(message)s"
        )
        logger.addHandler(_build_file_handler(log_path, fmt))

    _INTERVIEW_LOGGER_CACHE[cache_key] = logger
    return logger

ADMIN_API_HOST = os.getenv("ADMIN_API_HOST", "127.0.0.1")
ADMIN_API_PORT = int(os.getenv("ADMIN_API_PORT", "8890"))


def _extract_pcm_audio(raw_audio: bytes, log_fn: Callable[[str], None]) -> bytes:
    """Extract pure PCM bytes from nested length-prefixed audio payloads."""
    if not raw_audio:
        return b""

    def _strip_length_prefix(data: bytes) -> bytes:
        if len(data) < 4:
            return b""
        payload_size = int.from_bytes(data[:4], "big", signed=False)
        if payload_size <= 0 or payload_size > len(data) - 4:
            return b""
        return data[4 : 4 + payload_size]

    outer_payload = _strip_length_prefix(raw_audio)
    if not outer_payload:
        log_fn("[InterviewPersist] drop candidate audio frame: invalid outer payload")
        return b""

    try:
        parsed = parse_request(outer_payload)
        if isinstance(parsed, (bytes, bytearray)):
            inner_payload = bytes(parsed)
            pcm_payload = _strip_length_prefix(inner_payload)
            if pcm_payload:
                return pcm_payload
            log_fn("[InterviewPersist] drop candidate audio frame: invalid inner payload")
            return b""
    except Exception as parse_err:
        log_fn(
            f"[InterviewPersist] drop candidate audio frame: parse error={parse_err}"
        )
        return b""
    log_fn("[InterviewPersist] drop candidate audio frame: unsupported payload")
    return b""


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
        server_logger.info(
            "[Interview] rejected connection remote=%s token=%s",
            websocket.remote_address,
            token or "<empty>",
        )
        invalid_payload = BotErrorPayload(
            error=ErrorEvent(code="INVALID_TOKEN", message="面试链接无效或已失效")
        )
        await websocket.send(
            convert_web_event_to_binary(WebEvent.from_payload(invalid_payload))
        )
        await websocket.close()
        return

    interview_logger = _get_interview_logger(token, "backend")
    interview_log: Callable[[str], None] = interview_logger.info
    server_logger.info(
        "[Interview] started token=%s remote=%s",
        token,
        websocket.remote_address,
    )
    interview_log(f"[Session] started remote={websocket.remote_address}")

    turns = []
    candidate_audio = bytearray()
    interviewer_audio_encoded = bytearray()
    candidate_audio_dropped_frames = 0
    interview_completed = False

    def record_turn(role: str, text: str):
        if not text:
            return
        turns.append((role, text, datetime.now(timezone.utc).isoformat()))

    def on_interview_completed():
        nonlocal interview_completed
        interview_completed = True

    def record_bot_audio(chunk: bytes):
        interviewer_audio_encoded.extend(chunk)

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
        log_fn=interview_log,
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
        nonlocal candidate_audio_dropped_frames
        async for m in ws:
            input_event = convert_binary_to_web_event_to_binary(m)
            data_len = len(input_event.data) if input_event.data else 0
            interview_log(
                f"Received input event: {input_event.event}, \
                payload: {input_event.event}, data len:{data_len}"
            )
            if input_event.event == USER_AUDIO and input_event.data:
                pcm_bytes = _extract_pcm_audio(input_event.data, interview_log)
                if pcm_bytes:
                    candidate_audio.extend(pcm_bytes)
                else:
                    candidate_audio_dropped_frames += 1
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
            interview_log(
                f"Sending output event= {output_event.event}, \
                data len:{len(output_event.data) if output_event.data else 0} , payload: {output_event.payload}"
            )
            await ws.send(convert_web_event_to_binary(output_event))

    try:
        # Start the handler loop and asynchronously fetch output events
        outputs = service.handler_loop(async_gen(websocket))
        await asyncio.create_task(fetch_output(websocket, outputs))
    except websockets.exceptions.ConnectionClosed as e:
        interview_log(f"Connection closed: {e}")
    finally:
        try:
            save_interview_turns(token, turns)
            persist_interview_audio(
                token=token,
                candidate_pcm_bytes=bytes(candidate_audio),
                interviewer_encoded_bytes=bytes(interviewer_audio_encoded),
            )
            interview_log(
                f"[InterviewPersist] token={token} "
                f"candidate_bytes={len(candidate_audio)} "
                f"interviewer_bytes={len(interviewer_audio_encoded)} "
                f"candidate_dropped_frames={candidate_audio_dropped_frames}"
            )
            if interview_completed:
                mark_interview_completed(token)
                end_status = "completed"
            else:
                mark_interview_disconnected(token, grace_seconds=30)
                end_status = "disconnected"
            interview_log(f"[Session] closed status={end_status}")
            server_logger.info(
                "[Interview] closed token=%s remote=%s status=%s",
                token,
                websocket.remote_address,
                end_status,
            )
        except Exception as persist_err:
            interview_log(f"[InterviewPersist] failed token={token} error={persist_err}")
            server_logger.info(
                "[Interview] finalize failed token=%s remote=%s error=%s",
                token,
                websocket.remote_address,
                persist_err,
            )


def _http_response(status_line: bytes, body: bytes, *, cors: bool = False) -> bytes:
    headers = [status_line, f"Content-Length: {len(body)}".encode()]
    if cors:
        headers.append(b"Access-Control-Allow-Origin: *")
    return b"\r\n".join([*headers, b"", body])


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

    method, raw_path, _ = request_line.decode().split(" ", 2)
    parsed_path = urlparse(raw_path)
    request_path = parsed_path.path
    token = (parse_qs(parsed_path.query).get("token", [None])[0] or "").strip()

    if method == "POST" and request_path == "/api/frontend-logs":
        if not token or not interview_exists(token):
            response = _http_response(
                b"HTTP/1.1 400 Bad Request",
                b"Bad Request",
                cors=True,
            )
            writer.write(response)
            await writer.drain()
            writer.close()
            return

        content_length = int(headers.get("content-length", 0))
        body = await reader.readexactly(content_length)
        try:
            log_entries = json.loads(body)
            if not isinstance(log_entries, list):
                raise ValueError("log payload must be a list")

            frontend_logger = _get_interview_logger(token, "frontend")
            for entry in log_entries:
                frontend_logger.info(str(entry))
            response = _http_response(
                b"HTTP/1.1 200 OK",
                b"OK",
                cors=True,
            )
        except Exception:
            response = _http_response(
                b"HTTP/1.1 400 Bad Request",
                b"Bad Request",
                cors=True,
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
        response = _http_response(
            b"HTTP/1.1 404 Not Found",
            b"Not Found",
        )

    writer.write(response)
    await writer.drain()
    writer.close()


async def main():
    """
    Main function to start the WebSocket server and HTTP log server.
    """
    server_logger.info("[Server] startup begin")
    server_logger.info("[StartupSelfCheck] running preflight checks")
    self_check_report = await run_startup_self_check(RUNTIME_CONFIG)
    for line in format_self_check_lines(self_check_report):
        server_logger.info(line)
    if not self_check_report.ok:
        server_logger.info("[StartupSelfCheck] failed, aborting server startup")
        raise SystemExit(1)

    # Start the WebSocket server listening on 127.0.0.1:8888
    ws_server = await websockets.serve(handler, host="127.0.0.1", port=8888)
    server_logger.info("WebSocket server is running on ws://127.0.0.1:8888")

    # Start the HTTP log server on port 8889
    http_server = await asyncio.start_server(
        handle_frontend_log_request, host="127.0.0.1", port=8889
    )
    server_logger.info("HTTP log server is running on http://127.0.0.1:8889")

    admin_app = create_admin_app()
    admin_config = uvicorn.Config(
        app=admin_app,
        host=ADMIN_API_HOST,
        port=ADMIN_API_PORT,
        log_level="info",
        loop="asyncio",
    )
    admin_server = uvicorn.Server(admin_config)
    server_logger.info(f"Admin API server is running on http://{ADMIN_API_HOST}:{ADMIN_API_PORT}")

    try:
        await asyncio.gather(
            ws_server.wait_closed(),
            http_server.serve_forever(),
            admin_server.serve(),
        )
    finally:
        server_logger.info("[Server] shutdown")


if __name__ == "__main__":
    get_event_loop(main())
