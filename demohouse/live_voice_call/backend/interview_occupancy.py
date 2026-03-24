import os
import time
from dataclasses import dataclass
from typing import Literal, Optional

from redis import Redis
from redis.exceptions import RedisError

AcquireResult = Literal["admitted", "duplicate_token", "capacity_full", "error"]
HeartbeatResult = Literal["ok", "lost_lock", "error"]

_ACQUIRE_SCRIPT = """
redis.call("ZREMRANGEBYSCORE", KEYS[2], "-inf", ARGV[3])
local current_owner = redis.call("GET", KEYS[1])
if current_owner then
    if current_owner == ARGV[2] then
        redis.call("PEXPIRE", KEYS[1], ARGV[4])
        redis.call("ZADD", KEYS[2], ARGV[3] + ARGV[4], ARGV[1])
        return "admitted"
    end
    return "duplicate_token"
end
local active = redis.call("ZCARD", KEYS[2])
if active >= tonumber(ARGV[5]) then
    return "capacity_full"
end
local ok = redis.call("SET", KEYS[1], ARGV[2], "PX", ARGV[4], "NX")
if not ok then
    local race_owner = redis.call("GET", KEYS[1])
    if race_owner then
        return "duplicate_token"
    end
    return "capacity_full"
end
redis.call("ZADD", KEYS[2], ARGV[3] + ARGV[4], ARGV[1])
return "admitted"
"""

_HEARTBEAT_SCRIPT = """
local current_owner = redis.call("GET", KEYS[1])
if not current_owner or current_owner ~= ARGV[2] then
    return "lost_lock"
end
redis.call("PEXPIRE", KEYS[1], ARGV[4])
redis.call("ZADD", KEYS[2], ARGV[3] + ARGV[4], ARGV[1])
return "ok"
"""

_RELEASE_SCRIPT = """
local current_owner = redis.call("GET", KEYS[1])
if current_owner and current_owner == ARGV[2] then
    redis.call("DEL", KEYS[1])
    redis.call("ZREM", KEYS[2], ARGV[1])
    return 1
end
return 0
"""


@dataclass(frozen=True)
class OccupancyConfig:
    redis_url: str
    key_prefix: str
    ttl_seconds: int
    heartbeat_seconds: int
    max_active: int


def load_occupancy_config(*, max_active: int) -> OccupancyConfig:
    return OccupancyConfig(
        redis_url=(os.getenv("REDIS_URL") or "").strip(),
        key_prefix=(os.getenv("INTERVIEW_OCCUPANCY_KEY_PREFIX") or "interview:occupancy").strip(),
        ttl_seconds=max(3, int(os.getenv("INTERVIEW_OCCUPANCY_TTL_SECONDS", "30"))),
        heartbeat_seconds=max(
            1, int(os.getenv("INTERVIEW_OCCUPANCY_HEARTBEAT_SECONDS", "10"))
        ),
        max_active=max(1, int(max_active)),
    )


class InterviewOccupancy:
    def __init__(self, config: OccupancyConfig):
        self._config = config
        self._client: Optional[Redis] = None

    @property
    def config(self) -> OccupancyConfig:
        return self._config

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

    def _lock_key(self, token: str) -> str:
        return f"{self._config.key_prefix}:lock:{token}"

    def _active_key(self) -> str:
        return f"{self._config.key_prefix}:active"

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _ttl_ms(self) -> int:
        return self._config.ttl_seconds * 1000

    def acquire(self, token: str, owner_id: str) -> AcquireResult:
        client = self._get_client()
        if client is None:
            return "error"
        try:
            result = client.eval(
                _ACQUIRE_SCRIPT,
                2,
                self._lock_key(token),
                self._active_key(),
                token,
                owner_id,
                self._now_ms(),
                self._ttl_ms(),
                self._config.max_active,
            )
        except RedisError:
            return "error"
        if result in ("admitted", "duplicate_token", "capacity_full"):
            return result
        return "error"

    def heartbeat(self, token: str, owner_id: str) -> HeartbeatResult:
        client = self._get_client()
        if client is None:
            return "error"
        try:
            result = client.eval(
                _HEARTBEAT_SCRIPT,
                2,
                self._lock_key(token),
                self._active_key(),
                token,
                owner_id,
                self._now_ms(),
                self._ttl_ms(),
            )
        except RedisError:
            return "error"
        if result in ("ok", "lost_lock"):
            return result
        return "error"

    def release(self, token: str, owner_id: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            result = client.eval(
                _RELEASE_SCRIPT,
                2,
                self._lock_key(token),
                self._active_key(),
                token,
                owner_id,
            )
        except RedisError:
            return False
        return bool(result)
