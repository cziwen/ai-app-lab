import time
from urllib.parse import parse_qs, urlparse

import pytest

import stt_audio_url


def test_build_signed_audio_url_contains_sig_and_exp():
    url = stt_audio_url.build_signed_audio_url(
        base_url="https://api.example.com",
        token="abc123",
        track="candidate",
        secret="s3cr3t",
        ttl_seconds=600,
        now_ts=1700000000,
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.example.com"
    assert parsed.path == "/api/public/interviews/abc123/audio/candidate"
    qs = parse_qs(parsed.query)
    assert "exp" in qs and "sig" in qs
    assert qs["exp"][0] == "1700000600"


def test_verify_audio_access_signature_accepts_valid_sig():
    exp = int(time.time()) + 600
    sig = stt_audio_url.sign_audio_access(
        "token-1",
        "candidate",
        exp,
        secret="secret",
    )
    ok = stt_audio_url.verify_audio_access_signature(
        "token-1",
        "candidate",
        exp,
        sig,
        secret="secret",
    )
    assert ok is True


def test_verify_audio_access_signature_rejects_expired_or_tampered():
    exp = int(time.time()) + 600
    sig = stt_audio_url.sign_audio_access(
        "token-1",
        "candidate",
        exp,
        secret="secret",
    )
    assert (
        stt_audio_url.verify_audio_access_signature(
            "token-1",
            "candidate",
            exp,
            sig,
            secret="secret",
            now_ts=exp + 1,
        )
        is False
    )
    assert (
        stt_audio_url.verify_audio_access_signature(
            "token-2",
            "candidate",
            exp,
            sig,
            secret="secret",
        )
        is False
    )


def test_build_signed_audio_url_rejects_invalid_track():
    with pytest.raises(ValueError):
        stt_audio_url.build_signed_audio_url(
            base_url="https://api.example.com",
            token="abc123",
            track="bot",
            secret="s3cr3t",
            ttl_seconds=600,
        )
