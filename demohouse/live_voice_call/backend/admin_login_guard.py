import os
from dataclasses import dataclass
from typing import Optional, Tuple

from redis import Redis
from redis.exceptions import RedisError


@dataclass(frozen=True)
class AdminLoginGuardConfig:
    redis_url: str
    key_prefix: str
    fail_window_seconds: int
    lock_start: int
    lock_max_seconds: int


def load_admin_login_guard_config() -> AdminLoginGuardConfig:
    return AdminLoginGuardConfig(
        redis_url=(os.getenv("REDIS_URL") or "").strip(),
        key_prefix=(os.getenv("ADMIN_LOGIN_GUARD_PREFIX") or "admin:login:guard").strip(),
        fail_window_seconds=max(60, int(os.getenv("ADMIN_LOGIN_FAIL_WINDOW_SECONDS", "900"))),
        lock_start=max(1, int(os.getenv("ADMIN_LOGIN_LOCK_START", "5"))),
        lock_max_seconds=max(1, int(os.getenv("ADMIN_LOGIN_LOCK_MAX_SECONDS", "60"))),
    )


class AdminLoginGuard:
    def __init__(self, config: AdminLoginGuardConfig):
        self._config = config
        self._client: Optional[Redis] = None

    @property
    def available(self) -> bool:
        return bool(self._get_client())

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

    def _normalize_username(self, username: str) -> str:
        return (username or "").strip().lower()

    def _normalize_ip(self, ip: str) -> str:
        return (ip or "").strip() or "-"

    def _fail_ip_key(self, ip: str) -> str:
        return f"{self._config.key_prefix}:fail:ip:{self._normalize_ip(ip)}"

    def _fail_user_ip_key(self, username: str, ip: str) -> str:
        return (
            f"{self._config.key_prefix}:fail:userip:"
            f"{self._normalize_username(username)}:{self._normalize_ip(ip)}"
        )

    def _lock_ip_key(self, ip: str) -> str:
        return f"{self._config.key_prefix}:lock:ip:{self._normalize_ip(ip)}"

    def _lock_user_ip_key(self, username: str, ip: str) -> str:
        return (
            f"{self._config.key_prefix}:lock:userip:"
            f"{self._normalize_username(username)}:{self._normalize_ip(ip)}"
        )

    def _compute_lock_seconds(self, fail_count: int) -> int:
        if fail_count < self._config.lock_start:
            return 0
        step = fail_count - self._config.lock_start
        return min(self._config.lock_max_seconds, 2 ** step)

    def _safe_ttl(self, key: str) -> int:
        client = self._get_client()
        if client is None:
            return 0
        try:
            ttl = int(client.ttl(key))
        except (RedisError, TypeError, ValueError):
            return 0
        return max(0, ttl)

    def _safe_int(self, value: Optional[str]) -> int:
        if value is None:
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def get_failure_counts(self, username: str, ip: str) -> Tuple[int, int]:
        client = self._get_client()
        if client is None:
            return 0, 0
        try:
            values = client.mget(
                self._fail_ip_key(ip),
                self._fail_user_ip_key(username, ip),
            )
        except RedisError:
            return 0, 0
        if not isinstance(values, list) or len(values) != 2:
            return 0, 0
        return self._safe_int(values[0]), self._safe_int(values[1])

    def get_retry_after(self, username: str, ip: str) -> int:
        ttl_ip = self._safe_ttl(self._lock_ip_key(ip))
        ttl_user_ip = self._safe_ttl(self._lock_user_ip_key(username, ip))
        return max(ttl_ip, ttl_user_ip)

    def is_locked(self, username: str, ip: str) -> Tuple[bool, int]:
        retry_after = self.get_retry_after(username, ip)
        return retry_after > 0, retry_after

    def record_failure(self, username: str, ip: str) -> Tuple[int, int, int]:
        client = self._get_client()
        if client is None:
            return 0, 0, 0
        fail_ip_key = self._fail_ip_key(ip)
        fail_user_ip_key = self._fail_user_ip_key(username, ip)
        lock_ip_key = self._lock_ip_key(ip)
        lock_user_ip_key = self._lock_user_ip_key(username, ip)
        try:
            pipe = client.pipeline()
            pipe.incr(fail_ip_key)
            pipe.expire(fail_ip_key, self._config.fail_window_seconds)
            pipe.incr(fail_user_ip_key)
            pipe.expire(fail_user_ip_key, self._config.fail_window_seconds)
            results = pipe.execute()
            fail_count_ip = self._safe_int(results[0] if len(results) > 0 else None)
            fail_count_user_ip = self._safe_int(results[2] if len(results) > 2 else None)

            lock_seconds_ip = self._compute_lock_seconds(fail_count_ip)
            lock_seconds_user_ip = self._compute_lock_seconds(fail_count_user_ip)
            if lock_seconds_ip > 0:
                client.setex(lock_ip_key, lock_seconds_ip, "1")
            if lock_seconds_user_ip > 0:
                client.setex(lock_user_ip_key, lock_seconds_user_ip, "1")
            return fail_count_ip, fail_count_user_ip, max(lock_seconds_ip, lock_seconds_user_ip)
        except RedisError:
            return 0, 0, 0

    def clear_success(self, username: str, ip: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(
                self._fail_ip_key(ip),
                self._fail_user_ip_key(username, ip),
                self._lock_ip_key(ip),
                self._lock_user_ip_key(username, ip),
            )
        except RedisError:
            return
