import asyncio
import contextlib
import gzip
import json
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterable, AsyncIterator, Callable, Dict, List, Optional, Protocol

try:
    import websockets  # type: ignore
    from websockets.exceptions import ConnectionClosed  # type: ignore
except Exception:  # pragma: no cover - exercised only in minimal dev environments.
    websockets = None  # type: ignore

    class ConnectionClosed(Exception):
        pass

DEFAULT_ASR_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
DEFAULT_ASR_RESOURCE_ID = "volc.bigasr.sauc.duration"


class ProtocolVersion:
    V1 = 0b0001


class MessageType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ERROR_RESPONSE = 0b1111


class MessageTypeSpecificFlags:
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    LAST_PACKAGE = 0b0010
    LAST_PACKAGE_WITH_SEQUENCE = 0b0011


class SerializationType:
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001


class CompressionType:
    NO_COMPRESSION = 0b0000
    GZIP = 0b0001


@dataclass
class SaucASRResult:
    text: str = ""
    utterances: List[Any] = field(default_factory=list)


@dataclass
class SaucASRAudio:
    duration: int = 0


@dataclass
class SaucASRFullServerResponse:
    result: Optional[SaucASRResult] = None
    audio: Optional[SaucASRAudio] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedServerFrame:
    message_type: int
    is_last_package: bool
    payload_sequence: Optional[int]
    payload_obj: Optional[Dict[str, Any]]
    error_code: Optional[int] = None
    error_message: Optional[str] = None


class ASRClientProtocol(Protocol):
    inited: bool

    async def init(self) -> None:
        ...

    async def close(self) -> None:
        ...

    def stream_asr(
        self, source: AsyncIterable[bytes]
    ) -> AsyncIterable[SaucASRFullServerResponse]:
        ...


class SaucProtocolCodec:
    @staticmethod
    def _build_header(
        *,
        message_type: int,
        message_type_specific_flags: int,
        serialization: int,
        compression: int,
    ) -> bytes:
        return bytes(
            [
                (ProtocolVersion.V1 << 4) | 0x01,
                (message_type << 4) | message_type_specific_flags,
                (serialization << 4) | compression,
                0x00,
            ]
        )

    @staticmethod
    def build_full_client_request(payload: Dict[str, Any]) -> bytes:
        header = SaucProtocolCodec._build_header(
            message_type=MessageType.CLIENT_FULL_REQUEST,
            message_type_specific_flags=MessageTypeSpecificFlags.NO_SEQUENCE,
            serialization=SerializationType.JSON,
            compression=CompressionType.GZIP,
        )
        compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        packet = bytearray(header)
        packet.extend(struct.pack(">I", len(compressed)))
        packet.extend(compressed)
        return bytes(packet)

    @staticmethod
    def build_audio_only_request(audio_chunk: bytes, *, is_last: bool) -> bytes:
        header = SaucProtocolCodec._build_header(
            message_type=MessageType.CLIENT_AUDIO_ONLY_REQUEST,
            message_type_specific_flags=(
                MessageTypeSpecificFlags.LAST_PACKAGE
                if is_last
                else MessageTypeSpecificFlags.NO_SEQUENCE
            ),
            serialization=SerializationType.NO_SERIALIZATION,
            compression=CompressionType.GZIP,
        )
        compressed = gzip.compress(audio_chunk)
        packet = bytearray(header)
        packet.extend(struct.pack(">I", len(compressed)))
        packet.extend(compressed)
        return bytes(packet)

    @staticmethod
    def parse_server_frame(raw: bytes) -> ParsedServerFrame:
        if len(raw) < 8:
            raise ValueError("invalid SAUC frame: too short")

        header_size_units = raw[0] & 0x0F
        header_size = header_size_units * 4
        if header_size < 4 or len(raw) < header_size:
            raise ValueError("invalid SAUC frame: invalid header size")

        message_type = raw[1] >> 4
        flags = raw[1] & 0x0F
        serialization = raw[2] >> 4
        compression = raw[2] & 0x0F

        payload = raw[header_size:]
        payload_sequence = None
        if flags & 0x01:
            if len(payload) < 4:
                raise ValueError("invalid SAUC frame: missing sequence")
            payload_sequence = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]

        is_last_package = bool(flags & 0x02)
        if message_type == MessageType.SERVER_FULL_RESPONSE:
            if len(payload) < 4:
                raise ValueError("invalid SAUC frame: missing payload size")
            payload_size = struct.unpack(">I", payload[:4])[0]
            payload = payload[4:]
            if payload_size and len(payload) < payload_size:
                raise ValueError("invalid SAUC frame: truncated payload")
            payload = payload[:payload_size] if payload_size else payload
        elif message_type == MessageType.SERVER_ERROR_RESPONSE:
            if len(payload) < 8:
                raise ValueError("invalid SAUC error frame: too short")
            error_code = struct.unpack(">i", payload[:4])[0]
            payload_size = struct.unpack(">I", payload[4:8])[0]
            payload = payload[8:]
            if payload_size and len(payload) < payload_size:
                raise ValueError("invalid SAUC error frame: truncated payload")
            payload = payload[:payload_size] if payload_size else payload
            error_message = SaucProtocolCodec._decode_payload_to_text(
                payload, serialization=serialization, compression=compression
            )
            return ParsedServerFrame(
                message_type=message_type,
                is_last_package=is_last_package,
                payload_sequence=payload_sequence,
                payload_obj=None,
                error_code=error_code,
                error_message=error_message,
            )
        else:
            # Ignore unsupported message types but keep stream alive.
            return ParsedServerFrame(
                message_type=message_type,
                is_last_package=is_last_package,
                payload_sequence=payload_sequence,
                payload_obj=None,
            )

        payload_obj = SaucProtocolCodec._decode_payload_to_json(
            payload, serialization=serialization, compression=compression
        )
        return ParsedServerFrame(
            message_type=message_type,
            is_last_package=is_last_package,
            payload_sequence=payload_sequence,
            payload_obj=payload_obj,
        )

    @staticmethod
    def _decode_payload_to_text(
        payload: bytes, *, serialization: int, compression: int
    ) -> str:
        decoded = payload
        if compression == CompressionType.GZIP and payload:
            decoded = gzip.decompress(payload)
        if not decoded:
            return ""
        if serialization == SerializationType.JSON:
            try:
                obj = json.loads(decoded.decode("utf-8"))
                if isinstance(obj, dict):
                    return obj.get("message", "") or obj.get("error", "") or str(obj)
            except Exception:
                pass
        return decoded.decode("utf-8", errors="replace")

    @staticmethod
    def _decode_payload_to_json(
        payload: bytes, *, serialization: int, compression: int
    ) -> Optional[Dict[str, Any]]:
        decoded = payload
        if compression == CompressionType.GZIP and payload:
            decoded = gzip.decompress(payload)
        if not decoded:
            return None
        if serialization != SerializationType.JSON:
            return None
        obj = json.loads(decoded.decode("utf-8"))
        return obj if isinstance(obj, dict) else None


class SaucASRClient:
    def __init__(
        self,
        *,
        app_key: str,
        access_key: str,
        resource_id: str,
        ws_url: str = DEFAULT_ASR_WS_URL,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.app_key = (app_key or "").strip()
        self.access_key = (access_key or "").strip()
        self.resource_id = (resource_id or "").strip()
        self.ws_url = (ws_url or "").strip() or DEFAULT_ASR_WS_URL
        self.log_fn = log_fn
        self.inited = False
        self.connect_id = ""
        self.tt_logid = ""
        self._ws = None
        self._session_started = False
        self._stream_lock = asyncio.Lock()

    async def init(self) -> None:
        if self.inited and self._ws is not None and not self._ws.closed:
            return
        if websockets is None:
            raise RuntimeError("websockets dependency is required for SAUC ASR client")
        self._assert_config()
        self.connect_id = str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.app_key,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": self.connect_id,
        }
        self._ws = await websockets.connect(
            self.ws_url,
            extra_headers=headers,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
        self.inited = True
        self._session_started = False
        response_headers = getattr(self._ws, "response_headers", None) or {}
        self.tt_logid = (
            response_headers.get("X-Tt-Logid")
            or response_headers.get("x-tt-logid")
            or ""
        )
        returned_connect_id = (
            response_headers.get("X-Api-Connect-Id")
            or response_headers.get("x-api-connect-id")
            or ""
        )
        if returned_connect_id:
            self.connect_id = returned_connect_id
        self._log(
            f"ASR_WS_CONNECTED url={self.ws_url} connect_id={self.connect_id} "
            f"logid={self.tt_logid or '-'}"
        )

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None
        self.inited = False
        self._session_started = False

    def _mark_disconnected(self, reason: str) -> None:
        self.inited = False
        self._ws = None
        self._session_started = False
        self._log(
            "ASR_WS_DISCONNECTED "
            f"reason={reason} "
            f"connect_id={self.connect_id or '-'} "
            f"logid={self.tt_logid or '-'}"
        )

    async def _ensure_session_started(self) -> None:
        if not self.inited or self._ws is None or self._ws.closed:
            await self.init()
        if self._session_started:
            return
        full_req = SaucProtocolCodec.build_full_client_request(
            {
                "audio": {
                    "format": "pcm",
                    "codec": "raw",
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1,
                },
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "show_utterances": True,
                    "enable_nonstream": False,
                },
            }
        )
        await self._ws.send(full_req)
        self._session_started = True
        self._log("ASR_WS_FULL_REQUEST_SENT")

    async def _send_audio(self, audio_chunk: bytes, *, is_last: bool) -> None:
        try:
            await self._ensure_session_started()
            if not self.inited or self._ws is None or self._ws.closed:
                return
            req = SaucProtocolCodec.build_audio_only_request(audio_chunk, is_last=is_last)
            await self._ws.send(req)
        except ConnectionClosed as close_err:
            self._mark_disconnected(f"send_closed:{close_err}")

    def stream_asr(
        self, source: AsyncIterable[bytes]
    ) -> AsyncIterator[SaucASRFullServerResponse]:
        return self._stream_asr(source)

    async def _stream_asr(
        self, source: AsyncIterable[bytes]
    ) -> AsyncIterator[SaucASRFullServerResponse]:
        async with self._stream_lock:
            source_iter = source.__aiter__()
            pending_chunk_task: Optional[asyncio.Task] = asyncio.create_task(
                source_iter.__anext__()
            )
            recv_task: Optional[asyncio.Task] = None
            source_finished = False
            sent_last = False
            received_last = False
            try:
                while True:
                    if self.inited and self._ws is not None and not self._ws.closed:
                        if recv_task is None:
                            recv_task = asyncio.create_task(self._ws.recv())
                    elif recv_task is not None:
                        recv_task.cancel()
                        recv_task = None

                    wait_tasks = set()
                    if pending_chunk_task is not None:
                        wait_tasks.add(pending_chunk_task)
                    if recv_task is not None:
                        wait_tasks.add(recv_task)
                    if not wait_tasks:
                        break

                    done, _ = await asyncio.wait(
                        wait_tasks, return_when=asyncio.FIRST_COMPLETED
                    )

                    if recv_task is not None and recv_task in done:
                        try:
                            raw = recv_task.result()
                        except ConnectionClosed as close_err:
                            recv_task = None
                            self._mark_disconnected(f"recv_closed:{close_err}")
                        else:
                            recv_task = None
                            if isinstance(raw, str):
                                continue
                            frame = SaucProtocolCodec.parse_server_frame(raw)
                            if frame.error_code is not None:
                                raise RuntimeError(
                                    f"ASR server error code={frame.error_code} "
                                    f"message={frame.error_message or ''} "
                                    f"logid={self.tt_logid or '-'}"
                                )
                            if frame.payload_obj:
                                mapped = self._map_payload(frame.payload_obj)
                                if mapped is not None:
                                    yield mapped
                            if frame.is_last_package:
                                received_last = True

                    if pending_chunk_task is not None and pending_chunk_task in done:
                        try:
                            audio_chunk = pending_chunk_task.result()
                        except StopAsyncIteration:
                            source_finished = True
                            pending_chunk_task = None
                        else:
                            if audio_chunk:
                                await self._send_audio(bytes(audio_chunk), is_last=False)
                            pending_chunk_task = asyncio.create_task(source_iter.__anext__())

                    if source_finished:
                        if (
                            not sent_last
                            and self.inited
                            and self._ws is not None
                            and not self._ws.closed
                        ):
                            await self._send_audio(b"", is_last=True)
                            sent_last = True
                        if not self.inited or self._ws is None or self._ws.closed:
                            break
                        if sent_last and received_last:
                            break
            finally:
                if recv_task is not None:
                    recv_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await recv_task
                if pending_chunk_task is not None:
                    pending_chunk_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pending_chunk_task
                # Keep connection warm for the next stream unless it was closed externally.

    def _map_payload(
        self, payload_obj: Dict[str, Any]
    ) -> Optional[SaucASRFullServerResponse]:
        result_obj = payload_obj.get("result")
        if not isinstance(result_obj, dict):
            return None
        text = result_obj.get("text")
        utterances = result_obj.get("utterances")
        if text is None:
            text = ""
        if not isinstance(text, str):
            text = str(text)
        if not isinstance(utterances, list):
            utterances = []

        audio_info = payload_obj.get("audio_info") or payload_obj.get("audio") or {}
        duration = 0
        if isinstance(audio_info, dict):
            raw_duration = audio_info.get("duration", 0)
            if isinstance(raw_duration, (int, float)):
                duration = int(raw_duration)

        return SaucASRFullServerResponse(
            result=SaucASRResult(text=text, utterances=utterances),
            audio=SaucASRAudio(duration=duration),
            payload=payload_obj,
        )

    def _assert_config(self) -> None:
        if not self.app_key:
            raise ValueError("ASR_APP_ID missing")
        if not self.access_key:
            raise ValueError("ASR_ACCESS_TOKEN missing")
        if not self.resource_id:
            raise ValueError("ASR_RESOURCE_ID missing")

    def _log(self, message: str) -> None:
        if self.log_fn:
            with contextlib.suppress(Exception):
                self.log_fn(message)
