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
from logging.handlers import RotatingFileHandler
from typing import AsyncIterable

import websockets

from arkitect.telemetry.logger import INFO
from arkitect.utils.event_loop import get_event_loop
from service import VoiceBotService
from utils import *

ASR_ACCESS_TOKEN = "bnO29ab2sIHtKyt3f-Dn8SAYaMZr04BP"
ASR_APP_ID = "2057385740"
# replace with your tts API access
TTS_ACCESS_TOKEN = "bnO29ab2sIHtKyt3f-Dn8SAYaMZr04BP"
TTS_APP_ID = "2057385740"
# replace with your ark endpoint
LLM_ENDPOINT_ID = "ep-m-20260315140910-pfztd"

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


async def handler(websocket: websockets.WebSocketCommonProtocol, path):
    """
    Asynchronous function to handle WebSocket connections.

    Args:
        websocket (websockets.WebSocketCommonProtocol): The client's WebSocket connection.
        path (str): The requested path.
    """
    # Create a VoiceBotService instance and initialize it
    service = VoiceBotService(
        llm_ep_id=LLM_ENDPOINT_ID,
        tts_app_key=TTS_APP_ID,
        tts_access_key=TTS_ACCESS_TOKEN,
        asr_app_key=ASR_APP_ID,
        asr_access_key=ASR_ACCESS_TOKEN,
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
            INFO(
                f"Received input event: {input_event.event}, \
                payload: {input_event.event}, data len:{len(input_event.data)}"
            )
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

    # Start the WebSocket server listening on 127.0.0.1:8888
    ws_server = await websockets.serve(handler, host="127.0.0.1", port=8888)
    INFO("WebSocket server is running on ws://127.0.0.1:8888")

    # Start the HTTP log server on port 8889
    http_server = await asyncio.start_server(
        handle_frontend_log_request, host="127.0.0.1", port=8889
    )
    INFO("HTTP log server is running on http://127.0.0.1:8889")

    await asyncio.gather(ws_server.wait_closed(), http_server.serve_forever())


if __name__ == "__main__":
    get_event_loop(main())
