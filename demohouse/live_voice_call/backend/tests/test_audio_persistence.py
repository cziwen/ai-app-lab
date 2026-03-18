from pathlib import Path

import admin_store
import handler


def _audio_header(payload_size: int) -> bytes:
    header = bytearray(8)
    header[0] = 0x11  # protocol=1, header_size=1
    header[1] = 0x20  # audio-only request
    header[2] = 0x10  # json/no-compress (matches frontend)
    header[3] = 0x00
    header[4:8] = payload_size.to_bytes(4, "big", signed=False)
    return bytes(header)


def _wrap_twice_with_length_prefix(pcm: bytes) -> bytes:
    inner = _audio_header(len(pcm)) + pcm
    return len(inner).to_bytes(4, "big", signed=False) + inner


def _setup_tmp_store(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    storage_dir = data_dir / "storage"
    audio_dir = storage_dir / "audio"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(admin_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_store, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(admin_store, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(admin_store, "DB_PATH", db_path)


def test_extract_pcm_audio_unwraps_nested_payload():
    pcm = (b"\x01\x00\x02\x00") * 40
    raw_audio = _wrap_twice_with_length_prefix(pcm)
    extracted = handler._extract_pcm_audio(raw_audio)
    assert extracted == pcm


def test_extract_pcm_audio_drops_invalid_payload():
    assert handler._extract_pcm_audio(b"\x00\x00\x00") == b""
    assert handler._extract_pcm_audio(b"\x00\x00\x00\x08bad") == b""


def test_persist_interview_audio_saves_mp3_and_raw(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    admin_store.ensure_storage()

    token = "INT-audio-test"
    mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    out = admin_store.persist_interview_audio(
        token=token,
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=mp3_like,
    )
    assert out["interviewer_audio_path"] is not None
    assert out["interviewer_audio_path"].endswith("interviewer.mp3")

    raw_token = "INT-audio-raw"
    raw_like = b"\x10\x11\x12\x13" * 32
    out_raw = admin_store.persist_interview_audio(
        token=raw_token,
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=raw_like,
    )
    assert out_raw["interviewer_audio_path"] is not None
    assert out_raw["interviewer_audio_path"].endswith("interviewer.raw")
