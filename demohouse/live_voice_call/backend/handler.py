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
import contextlib
import json
import logging
import os
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, AsyncIterable, Callable, Deque, Dict, List, Optional, Tuple
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
from service import DEFAULT_SPEAKER, VoiceBotService
from startup_self_check import (
    format_self_check_lines,
    load_runtime_config,
    run_startup_self_check,
)
from async_log import build_async_rotating_handler
from llm_limiter import configure_llm_limit, get_llm_limit
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


def _build_server_boot_log_path() -> str:
    boot_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pid = os.getpid()
    return os.path.join(LOG_DIR, f"backend-{boot_timestamp}-p{pid}.log")


LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
SERVER_BOOT_LOG_PATH = _build_server_boot_log_path()
ASYNC_LOG_QUEUE_SIZE = int(os.getenv("ASYNC_LOG_QUEUE_SIZE", "10000"))
ASYNC_LOG_FLUSH_INTERVAL_MS = int(os.getenv("ASYNC_LOG_FLUSH_INTERVAL_MS", "200"))
ASYNC_LOG_DROP_POLICY = os.getenv("ASYNC_LOG_DROP_POLICY", "drop_oldest")
ASYNC_LOG_CLOSE_TIMEOUT_SECONDS = float(os.getenv("ASYNC_LOG_CLOSE_TIMEOUT_SECONDS", "5"))

MAX_ACTIVE_INTERVIEWS = int(os.getenv("MAX_ACTIVE_INTERVIEWS", "5"))
QUEUE_WAIT_TIMEOUT_SECONDS = int(os.getenv("QUEUE_WAIT_TIMEOUT_SECONDS", "1800"))
QUEUE_HEARTBEAT_SECONDS = max(1, int(os.getenv("QUEUE_HEARTBEAT_SECONDS", "5")))
PERSISTENCE_QUEUE_SIZE = int(os.getenv("PERSISTENCE_QUEUE_SIZE", "200"))
PERSISTENCE_MAX_RETRIES = int(os.getenv("PERSISTENCE_MAX_RETRIES", "3"))
PERSISTENCE_RETRY_BASE_SECONDS = float(
    os.getenv("PERSISTENCE_RETRY_BASE_SECONDS", "0.3")
)
PERSISTENCE_SHUTDOWN_TIMEOUT_SECONDS = float(
    os.getenv("PERSISTENCE_SHUTDOWN_TIMEOUT_SECONDS", "5")
)
INTERVIEW_LOGGER_CACHE_MAX = max(1, int(os.getenv("INTERVIEW_LOGGER_CACHE_MAX", "1000")))
INTERVIEW_LOGGER_IDLE_SECONDS = max(
    60, int(os.getenv("INTERVIEW_LOGGER_IDLE_SECONDS", "900"))
)
FRONTEND_LOG_MAX_BODY_BYTES = max(
    1024, int(os.getenv("FRONTEND_LOG_MAX_BODY_BYTES", "262144"))
)
FRONTEND_LOG_MAX_ENTRIES = max(1, int(os.getenv("FRONTEND_LOG_MAX_ENTRIES", "200")))
FRONTEND_LOG_MAX_ENTRY_CHARS = max(
    1, int(os.getenv("FRONTEND_LOG_MAX_ENTRY_CHARS", "2000"))
)

_INTERVIEW_LOGGER_CACHE: Dict[Tuple[str, str], logging.Logger] = {}
_INTERVIEW_LOGGER_LAST_USED: Dict[Tuple[str, str], float] = {}


def _build_file_handler(log_path: str, log_format: str):
    return build_async_rotating_handler(
        log_path=log_path,
        max_bytes=LOG_MAX_BYTES,
        backup_count=LOG_BACKUP_COUNT,
        log_format=log_format,
        queue_size=ASYNC_LOG_QUEUE_SIZE,
        flush_interval_ms=ASYNC_LOG_FLUSH_INTERVAL_MS,
        drop_policy=ASYNC_LOG_DROP_POLICY,
        close_timeout_seconds=ASYNC_LOG_CLOSE_TIMEOUT_SECONDS,
    )


server_logger = logging.getLogger("server")
server_logger.setLevel(logging.INFO)
server_logger.propagate = False


def _ensure_server_log_handler() -> None:
    if any(
        os.path.abspath(getattr(handler, "baseFilename", ""))
        == os.path.abspath(SERVER_BOOT_LOG_PATH)
        for handler in server_logger.handlers
    ):
        return
    server_logger.addHandler(
        _build_file_handler(
            SERVER_BOOT_LOG_PATH,
            "%(asctime)s - %(levelname)s - %(message)s",
        )
    )


_ensure_server_log_handler()


def _get_interview_logger(token: str, stream: str) -> logging.Logger:
    if stream not in ("backend", "frontend"):
        raise ValueError(f"unsupported stream: {stream}")

    cache_key = (token, stream)
    cached = _INTERVIEW_LOGGER_CACHE.get(cache_key)
    if cached:
        _INTERVIEW_LOGGER_LAST_USED[cache_key] = monotonic()
        return cached

    _prune_interview_logger_cache()

    interview_dir = os.path.join(str(INTERVIEW_LOG_DIR), token)
    os.makedirs(interview_dir, exist_ok=True)

    log_path = os.path.join(interview_dir, f"{stream}.log")
    logger = logging.getLogger(f"interview.{stream}.{token}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler_exists = any(
        os.path.abspath(getattr(handler, "baseFilename", "")) == os.path.abspath(log_path)
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
    _INTERVIEW_LOGGER_LAST_USED[cache_key] = monotonic()
    _prune_interview_logger_cache()
    return logger


def _close_logger_handlers(logger: logging.Logger) -> None:
    for logger_handler in list(logger.handlers):
        with contextlib.suppress(Exception):
            logger_handler.close()
        logger.removeHandler(logger_handler)


def _release_interview_logger(token: str, stream: str) -> bool:
    cache_key = (token, stream)
    logger = _INTERVIEW_LOGGER_CACHE.pop(cache_key, None)
    _INTERVIEW_LOGGER_LAST_USED.pop(cache_key, None)
    if not logger:
        return False
    _close_logger_handlers(logger)
    return True


def _release_interview_loggers_for_token(token: str) -> int:
    released = 0
    for stream in ("backend", "frontend"):
        if _release_interview_logger(token, stream):
            released += 1
    return released


def _prune_interview_logger_cache() -> None:
    now = monotonic()
    stale_keys = [
        cache_key
        for cache_key, last_used in _INTERVIEW_LOGGER_LAST_USED.items()
        if now - last_used >= INTERVIEW_LOGGER_IDLE_SECONDS
    ]
    for cache_key in stale_keys:
        _release_interview_logger(*cache_key)

    if len(_INTERVIEW_LOGGER_CACHE) <= INTERVIEW_LOGGER_CACHE_MAX:
        return

    ordered = sorted(
        _INTERVIEW_LOGGER_LAST_USED.items(),
        key=lambda item: item[1],
    )
    overflow_count = len(_INTERVIEW_LOGGER_CACHE) - INTERVIEW_LOGGER_CACHE_MAX
    for cache_key, _ in ordered[:overflow_count]:
        _release_interview_logger(*cache_key)

WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8888"))
LOG_HOST = os.getenv("LOG_HOST", "0.0.0.0")
LOG_PORT = int(os.getenv("LOG_PORT", "8889"))
ADMIN_API_HOST = os.getenv("ADMIN_API_HOST", "0.0.0.0")
ADMIN_API_PORT = int(os.getenv("ADMIN_API_PORT", "8890"))


@dataclass
class QueueWaiter:
    token: str
    admitted_event: asyncio.Event
    enqueued_at: float
    active_snapshot: int
    limit_snapshot: int


class AdmissionController:
    def __init__(self, max_active: int):
        self.max_active = max(1, int(max_active))
        self.active_tokens = set()
        self.active_counts: Dict[str, int] = {}
        self.wait_queue: Deque[QueueWaiter] = deque()
        self._lock = asyncio.Lock()

    async def acquire_or_enqueue(
        self, token: str
    ) -> Tuple[bool, Optional[QueueWaiter], bool]:
        async with self._lock:
            if token in self.active_tokens:
                self.active_counts[token] = self.active_counts.get(token, 0) + 1
                return True, None, False
            if len(self.active_tokens) < self.max_active:
                self.active_tokens.add(token)
                self.active_counts[token] = 1
                return True, None, False

            for waiter in self.wait_queue:
                if waiter.token == token:
                    return False, waiter, True

            waiter = QueueWaiter(
                token=token,
                admitted_event=asyncio.Event(),
                enqueued_at=monotonic(),
                active_snapshot=len(self.active_tokens),
                limit_snapshot=self.max_active,
            )
            self.wait_queue.append(waiter)
            return False, waiter, False

    async def remove_waiter(self, token: str) -> bool:
        async with self._lock:
            for waiter in list(self.wait_queue):
                if waiter.token == token:
                    self.wait_queue.remove(waiter)
                    return True
        return False

    async def release(self, token: str) -> None:
        async with self._lock:
            current = self.active_counts.get(token, 0)
            if current > 1:
                self.active_counts[token] = current - 1
                return
            self.active_counts.pop(token, None)
            self.active_tokens.discard(token)
            while self.wait_queue and len(self.active_tokens) < self.max_active:
                waiter = self.wait_queue.popleft()
                if waiter.admitted_event.is_set():
                    continue
                self.active_tokens.add(waiter.token)
                self.active_counts[waiter.token] = 1
                waiter.admitted_event.set()
                break

    async def snapshot(self, token: str) -> Tuple[int, int, int]:
        async with self._lock:
            active = len(self.active_tokens)
            if token in self.active_tokens:
                return 0, active, self.max_active
            for idx, waiter in enumerate(self.wait_queue, start=1):
                if waiter.token == token:
                    return idx, active, self.max_active
            return -1, active, self.max_active


@dataclass
class PersistenceTask:
    token: str
    turns: list
    candidate_pcm_bytes: bytes
    interviewer_encoded_bytes: bytes
    interview_completed: bool
    candidate_audio_dropped_frames: int
    grace_seconds: int = 30
    retries: int = 0


class PersistenceQueue:
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._queue: "asyncio.Queue[Optional[PersistenceTask]]" = asyncio.Queue(
            maxsize=max(1, PERSISTENCE_QUEUE_SIZE)
        )
        self._worker_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def submit(self, task: PersistenceTask) -> None:
        await self._queue.put(task)

    async def shutdown(self, timeout_seconds: float) -> None:
        if not self._worker_task:
            return
        await self._queue.put(None)
        try:
            await asyncio.wait_for(self._worker_task, timeout=max(0.1, timeout_seconds))
        except asyncio.TimeoutError:
            self.logger.warning(
                "event=persistence_queue.shutdown_timeout action=cancel_worker"
            )
            self._worker_task.cancel()
            with contextlib.suppress(Exception):
                await self._worker_task

    async def _worker_loop(self) -> None:
        while True:
            task = await self._queue.get()
            if task is None:
                self._queue.task_done()
                return
            try:
                await self._process(task)
            finally:
                self._queue.task_done()

    async def _process(self, task: PersistenceTask) -> None:
        try:
            await asyncio.to_thread(save_interview_turns, task.token, task.turns)
            await asyncio.to_thread(
                persist_interview_audio,
                token=task.token,
                candidate_pcm_bytes=task.candidate_pcm_bytes,
                interviewer_encoded_bytes=task.interviewer_encoded_bytes,
            )
            self.logger.info(
                "event=interview_persist.success token=%s candidate_bytes=%s interviewer_bytes=%s candidate_dropped_frames=%s",
                task.token,
                len(task.candidate_pcm_bytes),
                len(task.interviewer_encoded_bytes),
                task.candidate_audio_dropped_frames,
            )
            if task.interview_completed:
                await asyncio.to_thread(mark_interview_completed, task.token)
            else:
                await asyncio.to_thread(
                    mark_interview_disconnected, task.token, task.grace_seconds
                )
        except Exception as persist_err:
            if task.retries >= PERSISTENCE_MAX_RETRIES:
                self.logger.error(
                    "event=interview_persist.failed token=%s retries=%s error=%s",
                    task.token,
                    task.retries,
                    persist_err,
                    exc_info=True,
                )
                return
            task.retries += 1
            delay = PERSISTENCE_RETRY_BASE_SECONDS * (2 ** (task.retries - 1))
            self.logger.warning(
                "event=interview_persist.retry token=%s retry=%s delay=%.2fs error=%s",
                task.token,
                task.retries,
                delay,
                persist_err,
                exc_info=True,
            )
            await asyncio.sleep(delay)
            await self._queue.put(task)


ADMISSION = AdmissionController(MAX_ACTIVE_INTERVIEWS)
PERSISTENCE = PersistenceQueue(server_logger)


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
    interview_data = (
        await asyncio.to_thread(start_interview_session, token) if token else None
    )
    if not interview_data:
        server_logger.warning(
            "event=interview.rejected reason=invalid_token remote=%s token=%s",
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

    admitted, waiter, duplicated_waiter = await ADMISSION.acquire_or_enqueue(token)
    if duplicated_waiter:
        duplicate_waiting_payload = BotErrorPayload(
            error=ErrorEvent(
                code="TOKEN_ALREADY_WAITING",
                message="该面试链接已在排队中，请不要重复打开多个页面。",
            )
        )
        await websocket.send(
            convert_web_event_to_binary(WebEvent.from_payload(duplicate_waiting_payload))
        )
        await websocket.close()
        return

    if not admitted:
        assert waiter is not None
        initial_position, initial_active, initial_limit = await ADMISSION.snapshot(token)
        await websocket.send(
            convert_web_event_to_binary(
                WebEvent.from_payload(
                    QueueEnteredPayload(
                        position=max(1, initial_position),
                        active=initial_active
                        if initial_active >= 0
                        else waiter.active_snapshot,
                        limit=initial_limit if initial_limit > 0 else waiter.limit_snapshot,
                    )
                )
            )
        )
        queue_wait_start = monotonic()
        while True:
            if websocket.closed:
                await ADMISSION.remove_waiter(token)
                return
            if waiter.admitted_event.is_set():
                break
            waited_seconds = monotonic() - queue_wait_start
            if waited_seconds >= QUEUE_WAIT_TIMEOUT_SECONDS:
                await ADMISSION.remove_waiter(token)
                await websocket.send(
                    convert_web_event_to_binary(
                        WebEvent.from_payload(
                            QueueTimeoutPayload(
                                wait_seconds=int(waited_seconds),
                            )
                        )
                    )
                )
                await websocket.close()
                return
            try:
                await asyncio.wait_for(
                    waiter.admitted_event.wait(),
                    timeout=QUEUE_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                position, active, limit = await ADMISSION.snapshot(token)
                if position < 0:
                    await websocket.send(
                        convert_web_event_to_binary(
                            WebEvent.from_payload(
                                QueueCancelledPayload(reason="queue_removed")
                            )
                        )
                    )
                    await websocket.close()
                    return
                await websocket.send(
                    convert_web_event_to_binary(
                        WebEvent.from_payload(
                            QueueUpdatePayload(
                                position=position,
                                active=active,
                                limit=limit,
                            )
                        )
                    )
                )

        _, active_after_admit, limit_after_admit = await ADMISSION.snapshot(token)
        await websocket.send(
            convert_web_event_to_binary(
                WebEvent.from_payload(
                    QueueAdmittedPayload(
                        active=active_after_admit,
                        limit=limit_after_admit,
                    )
                )
            )
        )

    interview_logger = _get_interview_logger(token, "backend")
    interview_log: Callable[[str], None] = interview_logger.info
    server_logger.info(
        "event=interview.started token=%s remote=%s",
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
        tts_speaker=RUNTIME_CONFIG.tts_speaker or DEFAULT_SPEAKER,
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
        await ADMISSION.release(token)
        try:
            await PERSISTENCE.submit(
                PersistenceTask(
                    token=token,
                    turns=turns,
                    candidate_pcm_bytes=bytes(candidate_audio),
                    interviewer_encoded_bytes=bytes(interviewer_audio_encoded),
                    interview_completed=interview_completed,
                    candidate_audio_dropped_frames=candidate_audio_dropped_frames,
                    grace_seconds=30,
                )
            )
            end_status = "completed" if interview_completed else "disconnected"
            interview_log(f"[Session] closed status={end_status}")
            server_logger.info(
                "event=interview.closed token=%s remote=%s status=%s",
                token,
                websocket.remote_address,
                end_status,
            )
        except Exception as persist_err:
            interview_log(f"[InterviewPersist] failed token={token} error={persist_err}")
            server_logger.error(
                "event=interview.finalize_failed token=%s remote=%s error=%s",
                token,
                websocket.remote_address,
                persist_err,
                exc_info=True,
            )
        finally:
            _release_interview_loggers_for_token(token)


def _http_response(status_line: bytes, body: bytes, *, cors: bool = False) -> bytes:
    headers = [
        status_line,
        b"Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(body)}".encode(),
    ]
    if cors:
        headers.append(b"Access-Control-Allow-Origin: *")
    return b"\r\n".join([*headers, b"", body])


async def _write_http_response(writer: asyncio.StreamWriter, response: bytes) -> None:
    writer.write(response)
    await writer.drain()
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


def _parse_request_line(request_line: bytes) -> Tuple[str, str]:
    line_text = request_line.decode("utf-8", errors="replace").strip()
    parts = line_text.split()
    if len(parts) < 2:
        raise ValueError("invalid_request_line")
    method = parts[0].upper()
    raw_path = parts[1]
    return method, raw_path


def _parse_content_length(headers: Dict[str, str]) -> int:
    raw = headers.get("content-length")
    if raw is None:
        raise ValueError("missing_content_length")
    try:
        content_length = int(raw)
    except ValueError as exc:
        raise ValueError("invalid_content_length") from exc
    if content_length < 0:
        raise ValueError("invalid_content_length")
    if content_length > FRONTEND_LOG_MAX_BODY_BYTES:
        raise ValueError("body_too_large")
    return content_length


def _normalize_frontend_log_entries(raw_entries: Any) -> List[str]:
    if not isinstance(raw_entries, list):
        raise ValueError("payload_not_list")
    if len(raw_entries) > FRONTEND_LOG_MAX_ENTRIES:
        raise ValueError("too_many_entries")
    entries: List[str] = []
    for idx, item in enumerate(raw_entries):
        entry = item if isinstance(item, str) else str(item)
        if len(entry) > FRONTEND_LOG_MAX_ENTRY_CHARS:
            raise ValueError(f"entry_too_long:{idx}")
        entries.append(entry)
    return entries


async def handle_frontend_log_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
):
    """Minimal HTTP handler for frontend log ingestion."""
    request_line = await reader.readline()
    if not request_line:
        writer.close()
        return

    headers = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        key, sep, value = line.decode("utf-8", errors="replace").partition(":")
        if not sep:
            continue
        headers[key.strip().lower()] = value.strip()

    try:
        method, raw_path = _parse_request_line(request_line)
    except ValueError as parse_err:
        server_logger.warning(
            "event=frontend_log.reject reason=%s request_line=%s",
            parse_err,
            request_line.decode("utf-8", errors="replace").strip(),
        )
        await _write_http_response(
            writer,
            _http_response(
                b"HTTP/1.1 400 Bad Request",
                b"Bad Request",
                cors=True,
            ),
        )
        return

    parsed_path = urlparse(raw_path)
    request_path = parsed_path.path
    token = (parse_qs(parsed_path.query).get("token", [None])[0] or "").strip()

    if method == "POST" and request_path == "/api/frontend-logs":
        token_exists = await asyncio.to_thread(interview_exists, token) if token else False
        if not token_exists:
            server_logger.warning(
                "event=frontend_log.reject reason=invalid_token token=%s",
                token or "<empty>",
            )
            await _write_http_response(
                writer,
                _http_response(
                    b"HTTP/1.1 400 Bad Request",
                    b"Bad Request",
                    cors=True,
                ),
            )
            return

        try:
            content_length = _parse_content_length(headers)
            body = await reader.readexactly(content_length)
            parsed_entries = json.loads(body)
            log_entries = _normalize_frontend_log_entries(parsed_entries)

            frontend_logger = _get_interview_logger(token, "frontend")
            for entry in log_entries:
                frontend_logger.info(entry)
            server_logger.info(
                "event=frontend_log.accept token=%s entries=%s body_bytes=%s",
                token,
                len(log_entries),
                len(body),
            )
            response = _http_response(
                b"HTTP/1.1 200 OK",
                b"OK",
                cors=True,
            )
        except asyncio.IncompleteReadError:
            server_logger.warning(
                "event=frontend_log.reject reason=incomplete_body token=%s",
                token,
            )
            response = _http_response(
                b"HTTP/1.1 400 Bad Request",
                b"Bad Request",
                cors=True,
            )
        except (ValueError, json.JSONDecodeError) as parse_err:
            server_logger.warning(
                "event=frontend_log.reject reason=%s token=%s",
                parse_err,
                token,
            )
            response = _http_response(
                b"HTTP/1.1 400 Bad Request",
                b"Bad Request",
                cors=True,
            )
        except Exception as log_err:
            server_logger.error(
                "event=frontend_log.reject reason=unexpected_error token=%s error=%s",
                token,
                log_err,
                exc_info=True,
            )
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

    await _write_http_response(writer, response)


async def main():
    """
    Main function to start the WebSocket server and HTTP log server.
    """
    server_logger.info("event=server.log_file.selected path=%s", SERVER_BOOT_LOG_PATH)
    configure_llm_limit(int(os.getenv("LLM_CONCURRENT_REQUESTS", "5")))
    PERSISTENCE.start()
    server_logger.info("event=server.startup.begin")
    server_logger.info(
        "event=server.config max_active=%s queue_timeout_seconds=%s queue_heartbeat_seconds=%s llm_limit=%s tts_speaker=%s frontend_log_max_body_bytes=%s frontend_log_max_entries=%s frontend_log_max_entry_chars=%s interview_logger_cache_max=%s interview_logger_idle_seconds=%s",
        MAX_ACTIVE_INTERVIEWS,
        QUEUE_WAIT_TIMEOUT_SECONDS,
        QUEUE_HEARTBEAT_SECONDS,
        get_llm_limit(),
        RUNTIME_CONFIG.tts_speaker or DEFAULT_SPEAKER,
        FRONTEND_LOG_MAX_BODY_BYTES,
        FRONTEND_LOG_MAX_ENTRIES,
        FRONTEND_LOG_MAX_ENTRY_CHARS,
        INTERVIEW_LOGGER_CACHE_MAX,
        INTERVIEW_LOGGER_IDLE_SECONDS,
    )
    server_logger.info("event=startup_self_check.begin")
    self_check_report = await run_startup_self_check(RUNTIME_CONFIG)
    for line in format_self_check_lines(self_check_report):
        server_logger.info(line)
    if not self_check_report.ok:
        server_logger.error("event=startup_self_check.failed action=abort")
        raise SystemExit(1)

    # Start the WebSocket server
    ws_server = await websockets.serve(handler, host=WS_HOST, port=WS_PORT)
    server_logger.info("event=server.ws.ready url=ws://%s:%s", WS_HOST, WS_PORT)

    # Start the HTTP log server
    http_server = await asyncio.start_server(
        handle_frontend_log_request, host=LOG_HOST, port=LOG_PORT
    )
    server_logger.info("event=server.http_log.ready url=http://%s:%s", LOG_HOST, LOG_PORT)

    admin_app = create_admin_app()
    admin_config = uvicorn.Config(
        app=admin_app,
        host=ADMIN_API_HOST,
        port=ADMIN_API_PORT,
        log_level="info",
        loop="asyncio",
    )
    admin_server = uvicorn.Server(admin_config)
    server_logger.info(
        "event=server.admin_api.ready url=http://%s:%s",
        ADMIN_API_HOST,
        ADMIN_API_PORT,
    )

    try:
        await asyncio.gather(
            ws_server.wait_closed(),
            http_server.serve_forever(),
            admin_server.serve(),
        )
    finally:
        await PERSISTENCE.shutdown(PERSISTENCE_SHUTDOWN_TIMEOUT_SECONDS)
        server_logger.info("event=server.shutdown")


if __name__ == "__main__":
    get_event_loop(main())
