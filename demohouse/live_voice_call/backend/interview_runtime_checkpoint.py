import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from redis import Redis
from redis.exceptions import RedisError


@dataclass(frozen=True)
class RuntimeCheckpointConfig:
    redis_url: str
    key_prefix: str
    ttl_seconds: int


def load_runtime_checkpoint_config() -> RuntimeCheckpointConfig:
    return RuntimeCheckpointConfig(
        redis_url=(os.getenv("REDIS_URL") or "").strip(),
        key_prefix=(
            os.getenv("INTERVIEW_RUNTIME_CHECKPOINT_KEY_PREFIX")
            or "interview:runtime:checkpoint"
        ).strip(),
        ttl_seconds=max(
            60, int(os.getenv("INTERVIEW_RUNTIME_CHECKPOINT_TTL_SECONDS", "86400"))
        ),
    )


class InterviewRuntimeCheckpointStore:
    def __init__(self, config: RuntimeCheckpointConfig):
        self._config = config
        self._client: Optional[Redis] = None

    def _get_client(self) -> Optional[Redis]:
        if not self._config.redis_url:
            return None
        if self._client is None:
            self._client = Redis.from_url(
                self._config.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._client

    def _key(self, token: str) -> str:
        return f"{self._config.key_prefix}:{token}"

    def load(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(self._key(token))
        except RedisError:
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def save(self, token: str, checkpoint: Dict[str, Any]) -> bool:
        if not token or not isinstance(checkpoint, dict):
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            body = json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"))
            client.set(
                self._key(token),
                body,
                ex=max(1, int(self._config.ttl_seconds)),
            )
        except (TypeError, ValueError, RedisError):
            return False
        return True

    def delete(self, token: str) -> bool:
        if not token:
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            client.delete(self._key(token))
        except RedisError:
            return False
        return True
