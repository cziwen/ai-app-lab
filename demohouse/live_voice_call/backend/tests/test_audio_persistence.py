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


def _legacy_nested_audio_payload(pcm: bytes) -> bytes:
    return _audio_header(len(pcm)) + pcm


def _setup_tmp_store(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    storage_dir = data_dir / "storage"
    audio_dir = storage_dir / "audio"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(admin_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_store, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(admin_store, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(admin_store, "DB_PATH", db_path)


def _prepare_interview_attempt(monkeypatch, tmp_path: Path, *, attempt_id: str):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setattr(
        admin_store,
        "INTERVIEW_EXPIRY_INDEX",
        type(
            "_FakeExpiryIndex",
            (),
            {
                "ensure_ready": staticmethod(lambda: True),
                "schedule": staticmethod(lambda _token, _expires_at: True),
                "remove": staticmethod(lambda _token: True),
                "due_tokens": staticmethod(lambda *_args, **_kwargs: []),
            },
        )(),
    )
    admin_store.ensure_storage()
    job = admin_store.create_job(
        name="音频测试岗位",
        duties="测试",
        requirements="测试",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目", "背景 职责 结果")],
    )
    job_detail = admin_store.get_job_detail(job["job_uid"])
    assert job_detail is not None
    question_followups = [
        {
            "question_id": int(item["id"]),
            "max_followups": 0,
            "max_clarifies": 0,
        }
        for item in job_detail["questions"]
    ]
    interview = admin_store.create_interview(
        candidate_name="音频测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=question_followups,
    )
    token = interview["token"]
    assert admin_store.mark_interview_in_progress(token) is True
    assert admin_store.start_interview_attempt(token, attempt_id, owner_id="owner-1") == 1
    return token


def test_extract_pcm_audio_accepts_raw_pcm():
    pcm = (b"\x01\x00\x02\x00") * 40
    extracted = handler._extract_pcm_audio(pcm)
    assert extracted == pcm


def test_extract_pcm_audio_strips_4byte_length_prefix():
    pcm = (b"\x01\x00\x02\x00") * 800  # 3200 bytes
    payload = len(pcm).to_bytes(4, "big", signed=False) + pcm
    stats = {}
    extracted = handler._extract_pcm_audio(payload, stats=stats)
    assert extracted == pcm
    assert stats.get("prefixed", 0) == 1
    assert stats.get("raw", 0) == 0
    assert stats.get("dropped", 0) == 0


def test_extract_pcm_audio_drops_invalid_or_legacy_payload():
    assert handler._extract_pcm_audio(b"\x01") == b""
    assert handler._extract_pcm_audio(_legacy_nested_audio_payload(b"\x00\x00")) == b""


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


def test_persist_interview_audio_compresses_candidate_and_interviewer_mp3(
    monkeypatch, tmp_path
):
    _setup_tmp_store(monkeypatch, tmp_path)
    admin_store.ensure_storage()
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "true")

    candidate_mp3 = b"ID3" + b"\x00" * 64
    interviewer_mp3 = b"ID3" + b"\x11" * 64

    monkeypatch.setattr(
        admin_store,
        "_ffmpeg_encode_pcm_to_mp3",
        lambda *args, **kwargs: candidate_mp3,
    )
    monkeypatch.setattr(
        admin_store,
        "_ffmpeg_reencode_mp3",
        lambda *args, **kwargs: interviewer_mp3,
    )

    token = "INT-audio-compress"
    out = admin_store.persist_interview_audio(
        token=token,
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 1600,
        interviewer_encoded_bytes=b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128,
    )

    assert out["candidate_audio_path"] is not None
    assert out["candidate_audio_path"].endswith("candidate.mp3")
    assert out["interviewer_audio_path"] is not None
    assert out["interviewer_audio_path"].endswith("interviewer.mp3")
    assert Path(out["candidate_audio_path"]).read_bytes() == candidate_mp3
    assert Path(out["interviewer_audio_path"]).read_bytes() == interviewer_mp3


def test_persist_interview_audio_falls_back_when_ffmpeg_fails(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    admin_store.ensure_storage()
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "true")
    monkeypatch.setattr(
        admin_store,
        "_ffmpeg_encode_pcm_to_mp3",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        admin_store,
        "_ffmpeg_reencode_mp3",
        lambda *args, **kwargs: None,
    )

    candidate_pcm = (b"\x01\x00\x02\x00") * 1600
    interviewer_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    token = "INT-audio-fallback"
    out = admin_store.persist_interview_audio(
        token=token,
        candidate_pcm_bytes=candidate_pcm,
        interviewer_encoded_bytes=interviewer_mp3,
    )

    assert out["candidate_audio_path"] is not None
    assert out["candidate_audio_path"].endswith("candidate.wav")
    assert out["interviewer_audio_path"] is not None
    assert out["interviewer_audio_path"].endswith("interviewer.mp3")
    assert Path(out["interviewer_audio_path"]).read_bytes() == interviewer_mp3


def test_persist_interview_question_audio_segments_saves_mp3(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "true")
    token = _prepare_interview_attempt(
        monkeypatch,
        tmp_path,
        attempt_id="attempt-question-mp3",
    )
    expected_mp3 = b"ID3" + b"\x00" * 64
    monkeypatch.setattr(
        admin_store,
        "_ffmpeg_encode_pcm_to_mp3",
        lambda *args, **kwargs: expected_mp3,
    )

    out = admin_store.persist_interview_question_audio_segments(
        token,
        attempt_id="attempt-question-mp3",
        question_candidate_pcm_segments=[
            {
                "question_id": "q1",
                "question_epoch": 0,
                "pcm_bytes": (b"\x01\x00\x02\x00") * 1200,
            }
        ],
    )

    assert out["saved_segment_count"] == 1
    with admin_store.get_conn() as conn:
        row = conn.execute(
            """
            SELECT file_path
            FROM interview_question_audio_segments
            WHERE interview_token = ?
            """,
            (token,),
        ).fetchone()
    assert row is not None
    saved_path = Path(str(row["file_path"]))
    assert saved_path.exists()
    assert "/questions/" in str(saved_path)
    assert saved_path.suffix == ".mp3"
    assert saved_path.read_bytes() == expected_mp3


def test_persist_interview_question_audio_segments_falls_back_to_wav(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "true")
    token = _prepare_interview_attempt(
        monkeypatch,
        tmp_path,
        attempt_id="attempt-question-wav",
    )
    monkeypatch.setattr(
        admin_store,
        "_ffmpeg_encode_pcm_to_mp3",
        lambda *args, **kwargs: None,
    )

    out = admin_store.persist_interview_question_audio_segments(
        token,
        attempt_id="attempt-question-wav",
        question_candidate_pcm_segments=[
            {
                "question_id": "q1",
                "question_epoch": 0,
                "pcm_bytes": (b"\x01\x00\x02\x00") * 1200,
            }
        ],
    )

    assert out["saved_segment_count"] == 1
    with admin_store.get_conn() as conn:
        row = conn.execute(
            """
            SELECT file_path
            FROM interview_question_audio_segments
            WHERE interview_token = ?
            """,
            (token,),
        ).fetchone()
    assert row is not None
    saved_path = Path(str(row["file_path"]))
    assert saved_path.exists()
    assert saved_path.suffix == ".wav"
