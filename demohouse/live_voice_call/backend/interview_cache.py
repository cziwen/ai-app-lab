import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from redis import Redis
from redis.exceptions import RedisError

CACHE_MISS = object()
_NULL_SENTINEL = "__null__"


def _parse_iso_or_none(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class InterviewTokenCache:
    def __init__(self) -> None:
        self.redis_url = (os.getenv("REDIS_URL") or "").strip()
        self.key_prefix = (os.getenv("INTERVIEW_CACHE_KEY_PREFIX") or "interview").strip()
        self.cache_ttl_seconds = max(1, int(os.getenv("INTERVIEW_CACHE_TTL_SECONDS", "60")))
        self.cache_negative_ttl_seconds = max(
            1, int(os.getenv("INTERVIEW_CACHE_NEGATIVE_TTL_SECONDS", "15"))
        )
        self._client: Optional[Redis] = None

    def _get_client(self) -> Optional[Redis]:
        if not self.redis_url:
            return None
        if self._client is None:
            self._client = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._client

    def _ttl_seconds(self, *, expires_at: Optional[str], negative: bool) -> int:
        ttl = self.cache_negative_ttl_seconds if negative else self.cache_ttl_seconds
        expires_at_dt = _parse_iso_or_none(expires_at)
        if not expires_at_dt:
            return ttl
        remaining = int((expires_at_dt - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            return 1
        return max(1, min(ttl, remaining))

    def _exists_key(self, token: str) -> str:
        return f"{self.key_prefix}:exists:{token}"

    def _public_access_key(self, token: str) -> str:
        return f"{self.key_prefix}:public_access:{token}"

    def get_exists(self, token: str) -> Optional[bool]:
        client = self._get_client()
        if client is None:
            return None
        try:
            value = client.get(self._exists_key(token))
            if value is None:
                return None
            return value == "1"
        except RedisError:
            return None

    def set_exists(self, token: str, exists: bool, *, expires_at: Optional[str]) -> None:
        client = self._get_client()
        if client is None:
            return
        ttl = self._ttl_seconds(expires_at=expires_at, negative=not exists)
        payload = "1" if exists else "0"
        try:
            client.setex(self._exists_key(token), ttl, payload)
        except RedisError:
            return

    def get_public_access(self, token: str) -> Any:
        client = self._get_client()
        if client is None:
            return CACHE_MISS
        try:
            value = client.get(self._public_access_key(token))
            if value is None:
                return CACHE_MISS
            if value == _NULL_SENTINEL:
                return None
            payload = json.loads(value)
            if isinstance(payload, dict):
                return payload
            return CACHE_MISS
        except (RedisError, json.JSONDecodeError):
            return CACHE_MISS

    def set_public_access(
        self,
        token: str,
        payload: Optional[Dict[str, Any]],
        *,
        expires_at: Optional[str],
    ) -> None:
        client = self._get_client()
        if client is None:
            return
        ttl = self._ttl_seconds(expires_at=expires_at, negative=payload is None)
        value = _NULL_SENTINEL if payload is None else json.dumps(payload, ensure_ascii=False)
        try:
            client.setex(self._public_access_key(token), ttl, value)
        except RedisError:
            return

    def invalidate(self, token: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(self._exists_key(token), self._public_access_key(token))
        except RedisError:
            return

