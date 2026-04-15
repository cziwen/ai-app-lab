import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import quote


def _normalize_track(track: str) -> str:
    normalized = str(track or "").strip()
    if normalized not in {"candidate", "interviewer"}:
        raise ValueError(f"invalid track: {track}")
    return normalized


def _normalize_token(token: str) -> str:
    normalized = str(token or "").strip()
    if not normalized:
        raise ValueError("token is required")
    return normalized


def _normalize_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("base_url is required")
    if not (normalized.startswith("http://") or normalized.startswith("https://")):
        raise ValueError("base_url must start with http:// or https://")
    return normalized


def _normalize_secret(secret: str) -> str:
    normalized = str(secret or "").strip()
    if not normalized:
        raise ValueError("signing secret is required")
    return normalized


def _payload(token: str, track: str, exp: int) -> bytes:
    return f"{token}:{track}:{int(exp)}".encode("utf-8")


def sign_audio_access(token: str, track: str, exp: int, *, secret: str) -> str:
    normalized_token = _normalize_token(token)
    normalized_track = _normalize_track(track)
    normalized_secret = _normalize_secret(secret)
    mac = hmac.new(
        normalized_secret.encode("utf-8"),
        _payload(normalized_token, normalized_track, int(exp)),
        hashlib.sha256,
    )
    return mac.hexdigest()


def verify_audio_access_signature(
    token: str,
    track: str,
    exp: int,
    sig: str,
    *,
    secret: str,
    now_ts: Optional[int] = None,
) -> bool:
    normalized_sig = str(sig or "").strip().lower()
    if not normalized_sig:
        return False
    try:
        normalized_exp = int(exp)
    except (TypeError, ValueError):
        return False
    current_ts = int(now_ts if now_ts is not None else time.time())
    if normalized_exp <= current_ts:
        return False
    expected = sign_audio_access(token, track, normalized_exp, secret=secret)
    return hmac.compare_digest(expected, normalized_sig)


def build_signed_audio_url(
    *,
    base_url: str,
    token: str,
    track: str,
    secret: str,
    ttl_seconds: int,
    now_ts: Optional[int] = None,
) -> str:
    normalized_base_url = _normalize_base_url(base_url)
    normalized_token = _normalize_token(token)
    normalized_track = _normalize_track(track)
    normalized_secret = _normalize_secret(secret)
    normalized_ttl = max(1, int(ttl_seconds))
    current_ts = int(now_ts if now_ts is not None else time.time())
    exp = current_ts + normalized_ttl
    sig = sign_audio_access(
        normalized_token,
        normalized_track,
        exp,
        secret=normalized_secret,
    )
    encoded_token = quote(normalized_token, safe="")
    return (
        f"{normalized_base_url}/api/public/interviews/{encoded_token}/audio/"
        f"{normalized_track}?exp={exp}&sig={sig}"
    )
