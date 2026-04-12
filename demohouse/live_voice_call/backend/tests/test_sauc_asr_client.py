import asyncio
import gzip
import json
import struct

import sauc_asr_client
from sauc_asr_client import SaucASRClient, SaucProtocolCodec


def test_build_full_client_request_uses_official_layout():
    payload = {
        "audio": {"format": "pcm", "codec": "raw"},
        "request": {"model_name": "bigmodel"},
    }
    frame = SaucProtocolCodec.build_full_client_request(payload)

    assert frame[0] == 0x11  # version=1, header_size=1
    assert frame[1] == 0x10  # full request + no sequence
    assert frame[2] == 0x11  # json + gzip
    payload_size = struct.unpack(">I", frame[4:8])[0]
    compressed = frame[8:]
    assert payload_size == len(compressed)
    assert json.loads(gzip.decompress(compressed).decode("utf-8")) == payload


def test_build_audio_only_request_marks_last_package_with_flag_0010():
    frame = SaucProtocolCodec.build_audio_only_request(b"\x00\x00" * 1600, is_last=False)
    assert frame[0] == 0x11
    assert frame[1] == 0x20  # audio-only + no sequence
    assert frame[2] == 0x01  # no-serialization + gzip

    last = SaucProtocolCodec.build_audio_only_request(b"", is_last=True)
    assert last[1] == 0x22  # audio-only + last package (0b0010)
    assert struct.unpack(">I", last[4:8])[0] == len(last[8:])


def test_parse_server_full_response_extracts_payload_and_sequence():
    payload_obj = {
        "result": {"text": "你好", "utterances": []},
        "audio_info": {"duration": 1200},
    }
    compressed = gzip.compress(json.dumps(payload_obj).encode("utf-8"))
    frame = (
        bytes([0x11, 0x91, 0x11, 0x00])
        + struct.pack(">i", 7)
        + struct.pack(">I", len(compressed))
        + compressed
    )

    parsed = SaucProtocolCodec.parse_server_frame(frame)
    assert parsed.message_type == 0b1001
    assert parsed.payload_sequence == 7
    assert parsed.payload_obj == payload_obj
    assert parsed.is_last_package is False


def test_parse_server_error_response_extracts_error_fields():
    payload_obj = {"message": "bad request"}
    payload = json.dumps(payload_obj).encode("utf-8")
    frame = (
        bytes([0x11, 0xF0, 0x10, 0x00])
        + struct.pack(">i", 45000001)
        + struct.pack(">I", len(payload))
        + payload
    )

    parsed = SaucProtocolCodec.parse_server_frame(frame)
    assert parsed.message_type == 0b1111
    assert parsed.error_code == 45000001
    assert "bad request" in (parsed.error_message or "")


def test_map_payload_to_response_keeps_result_text_utterances_and_duration():
    client = SaucASRClient(
        app_key="app",
        access_key="token",
        resource_id="volc.bigasr.sauc.duration",
    )
    mapped = client._map_payload(
        {
            "result": {"text": "hello", "utterances": [{"text": "hello"}]},
            "audio_info": {"duration": 345},
        },
        stream_connect_id="conn-123",
    )
    assert mapped is not None
    assert mapped.result is not None
    assert mapped.audio is not None
    assert mapped.result.text == "hello"
    assert mapped.result.utterances == [{"text": "hello"}]
    assert mapped.audio.duration == 345
    assert mapped.stream_connect_id == "conn-123"


def test_send_audio_connection_closed_resets_client_state(monkeypatch):
    class DummyConnectionClosed(Exception):
        pass

    class _SendCloseWS:
        closed = False

        async def send(self, _data):
            raise DummyConnectionClosed("send closed")

    async def _run():
        monkeypatch.setattr(sauc_asr_client, "ConnectionClosed", DummyConnectionClosed)
        client = SaucASRClient(
            app_key="app",
            access_key="token",
            resource_id="volc.bigasr.sauc.duration",
        )
        client.inited = True
        client._session_started = True
        client._ws = _SendCloseWS()

        await client._send_audio(b"\x00\x01", is_last=False)

        assert client.inited is False
        assert client._ws is None
        assert client._session_started is False

    asyncio.run(_run())


def test_stream_asr_send_close_is_recoverable(monkeypatch):
    class DummyConnectionClosed(Exception):
        pass

    class _SendCloseWS:
        closed = False

        async def send(self, _data):
            raise DummyConnectionClosed("send closed")

        async def recv(self):
            await asyncio.sleep(3600)
            return b""

    async def _source():
        yield b"\x00\x01"

    async def _run():
        monkeypatch.setattr(sauc_asr_client, "ConnectionClosed", DummyConnectionClosed)
        client = SaucASRClient(
            app_key="app",
            access_key="token",
            resource_id="volc.bigasr.sauc.duration",
        )
        client.inited = True
        client._session_started = True
        client._ws = _SendCloseWS()

        outputs = []
        async for item in client.stream_asr(_source()):
            outputs.append(item)

        assert outputs == []
        assert client.inited is False
        assert client._ws is None
        assert client._session_started is False

    asyncio.run(_run())


def _build_error_frame(error_code: int, message: str) -> bytes:
    payload = json.dumps({"message": message}).encode("utf-8")
    return (
        bytes([0x11, 0xF0, 0x10, 0x00])
        + struct.pack(">i", error_code)
        + struct.pack(">I", len(payload))
        + payload
    )


def _build_last_package_frame() -> bytes:
    return bytes([0x11, 0x92, 0x00, 0x00]) + struct.pack(">I", 0)


def _decode_audio_payload(frame: bytes) -> bytes:
    payload_size = struct.unpack(">I", frame[4:8])[0]
    compressed = frame[8 : 8 + payload_size]
    return gzip.decompress(compressed) if compressed else b""


def test_stream_asr_wait_next_packet_timeout_is_graceful_end():
    class _TimeoutWS:
        closed = False

        def __init__(self):
            self._returned = False

        async def send(self, _data):
            return None

        async def recv(self):
            if not self._returned:
                self._returned = True
                return _build_error_frame(45000081, "Timeout waiting next packet: 8s")
            await asyncio.sleep(3600)
            return b""

    async def _source():
        await asyncio.sleep(3600)
        if False:
            yield b""

    async def _run():
        logs = []
        client = SaucASRClient(
            app_key="app",
            access_key="token",
            resource_id="volc.bigasr.sauc.duration",
            log_fn=logs.append,
        )
        client.inited = True
        client._session_started = True
        client._ws = _TimeoutWS()

        outputs = []
        async for item in client.stream_asr(_source()):
            outputs.append(item)

        assert outputs == []
        assert client.inited is False
        assert client._ws is None
        assert any("ASR_SERVER_SESSION_TIMEOUT" in line for line in logs)

    asyncio.run(_run())


def test_stream_asr_buffers_ready_chunk_on_timeout_and_replays_next_stream():
    class _TimeoutWS:
        closed = False

        def __init__(self):
            self.sent = []
            self._returned = False

        async def send(self, data):
            self.sent.append(data)

        async def recv(self):
            if not self._returned:
                self._returned = True
                return _build_error_frame(45000081, "Timeout waiting next packet: 8s")
            await asyncio.sleep(3600)
            return b""

    class _LastPackageWS:
        closed = False

        def __init__(self):
            self.sent = []
            self._returned_last = False

        async def send(self, data):
            self.sent.append(data)

        async def recv(self):
            if not self._returned_last:
                self._returned_last = True
                return _build_last_package_frame()
            await asyncio.sleep(3600)
            return b""

    async def _first_source():
        yield b"replay-me"
        await asyncio.sleep(3600)

    async def _second_source():
        yield b"live-new"
        if False:
            yield b""

    async def _run():
        client = SaucASRClient(
            app_key="app",
            access_key="token",
            resource_id="volc.bigasr.sauc.duration",
        )

        first_ws = _TimeoutWS()
        client.inited = True
        client._session_started = True
        client._ws = first_ws

        async for _ in client.stream_asr(_first_source()):
            pass

        # Timeout happens before sending the just-ready chunk to old session.
        assert first_ws.sent == []

        second_ws = _LastPackageWS()
        client.inited = True
        client._session_started = True
        client._ws = second_ws

        async for _ in client.stream_asr(_second_source()):
            pass

        # Two non-empty chunks should be sent exactly once and in order:
        # replayed chunk first, then current-stream chunk.
        non_empty_payloads = []
        for frame in second_ws.sent:
            payload = _decode_audio_payload(frame)
            if payload:
                non_empty_payloads.append(payload)
        assert non_empty_payloads == [b"replay-me", b"live-new"]
        assert client._replay_audio_chunk is None

    asyncio.run(_run())


def test_stream_asr_non_timeout_error_still_raises():
    class _ErrorWS:
        closed = False

        async def send(self, _data):
            return None

        async def recv(self):
            return _build_error_frame(45000001, "bad request")

    async def _source():
        await asyncio.sleep(3600)
        if False:
            yield b""

    async def _run():
        client = SaucASRClient(
            app_key="app",
            access_key="token",
            resource_id="volc.bigasr.sauc.duration",
        )
        client.inited = True
        client._session_started = True
        client._ws = _ErrorWS()

        try:
            async for _ in client.stream_asr(_source()):
                pass
            assert False, "non-timeout server error should raise RuntimeError"
        except RuntimeError as err:
            assert "ASR server error code=45000001" in str(err)

    asyncio.run(_run())
