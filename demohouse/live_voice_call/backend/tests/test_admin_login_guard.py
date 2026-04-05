import time

import admin_login_guard as guard_mod


class _FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def incr(self, key: str):
        self.ops.append(("incr", key))
        return self

    def expire(self, key: str, seconds: int):
        self.ops.append(("expire", key, seconds))
        return self

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "incr":
                results.append(self.redis.incr(op[1]))
            elif op[0] == "expire":
                results.append(self.redis.expire(op[1], op[2]))
        self.ops = []
        return results


class _FakeRedis:
    def __init__(self):
        self._values = {}
        self._expires_at = {}

    def _sweep(self, key: str):
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return
        if time.time() >= expires_at:
            self._values.pop(key, None)
            self._expires_at.pop(key, None)

    def pipeline(self):
        return _FakePipeline(self)

    def incr(self, key: str):
        self._sweep(key)
        current = int(self._values.get(key, "0"))
        current += 1
        self._values[key] = str(current)
        return current

    def expire(self, key: str, seconds: int):
        self._sweep(key)
        if key not in self._values:
            return 0
        self._expires_at[key] = time.time() + max(0, int(seconds))
        return 1

    def setex(self, key: str, seconds: int, value: str):
        self._values[key] = str(value)
        self._expires_at[key] = time.time() + max(0, int(seconds))
        return True

    def ttl(self, key: str):
        self._sweep(key)
        if key not in self._values:
            return -2
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return -1
        return max(0, int(expires_at - time.time()))

    def mget(self, *keys):
        values = []
        for key in keys:
            self._sweep(key)
            values.append(self._values.get(key))
        return values

    def delete(self, *keys):
        deleted = 0
        for key in keys:
            self._sweep(key)
            if key in self._values:
                deleted += 1
                self._values.pop(key, None)
                self._expires_at.pop(key, None)
        return deleted


def _build_guard(monkeypatch, *, lock_start=5, lock_max=60, fail_window=900):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("ADMIN_LOGIN_LOCK_START", str(lock_start))
    monkeypatch.setenv("ADMIN_LOGIN_LOCK_MAX_SECONDS", str(lock_max))
    monkeypatch.setenv("ADMIN_LOGIN_FAIL_WINDOW_SECONDS", str(fail_window))
    monkeypatch.setattr(guard_mod.Redis, "from_url", lambda *args, **kwargs: fake_redis)
    return guard_mod.AdminLoginGuard(guard_mod.load_admin_login_guard_config()), fake_redis


def test_record_failure_uses_expected_backoff_curve(monkeypatch):
    guard, _ = _build_guard(monkeypatch, lock_start=5, lock_max=60)
    ip = "203.0.113.9"
    username = "admin"

    observed = []
    for _ in range(8):
        _, _, lock_seconds = guard.record_failure(username, ip)
        observed.append(lock_seconds)

    assert observed[:4] == [0, 0, 0, 0]
    assert observed[4:] == [1, 2, 4, 8]


def test_is_locked_prioritizes_higher_retry_after(monkeypatch):
    guard, fake_redis = _build_guard(monkeypatch, lock_start=5, lock_max=60)
    cfg = guard_mod.load_admin_login_guard_config()
    ip = "198.51.100.4"
    username = "admin"

    fake_redis.setex(f"{cfg.key_prefix}:lock:ip:{ip}", 3, "1")
    fake_redis.setex(f"{cfg.key_prefix}:lock:userip:{username}:{ip}", 7, "1")

    locked, retry_after = guard.is_locked(username, ip)
    assert locked is True
    assert retry_after >= 6


def test_clear_success_removes_failure_and_lock_state(monkeypatch):
    guard, _ = _build_guard(monkeypatch, lock_start=2, lock_max=10)
    ip = "192.0.2.11"
    username = "admin"

    guard.record_failure(username, ip)
    guard.record_failure(username, ip)
    locked, _ = guard.is_locked(username, ip)
    assert locked is True

    guard.clear_success(username, ip)

    fail_count_ip, fail_count_userip = guard.get_failure_counts(username, ip)
    locked_after_clear, retry_after = guard.is_locked(username, ip)
    assert fail_count_ip == 0
    assert fail_count_userip == 0
    assert locked_after_clear is False
    assert retry_after == 0
