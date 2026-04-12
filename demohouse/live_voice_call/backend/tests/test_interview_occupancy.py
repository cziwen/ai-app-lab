import time

import interview_occupancy as occupancy


class _FakeRedis:
    def __init__(self):
        self.locks = {}
        self.clients = {}
        self.active = {}

    def _cleanup(self, now_ms: int) -> None:
        expired_tokens = [
            token for token, (_, expires_ms) in self.locks.items() if expires_ms <= now_ms
        ]
        for token in expired_tokens:
            self.locks.pop(token, None)
            self.clients.pop(token, None)
        expired_active = [token for token, expires_ms in self.active.items() if expires_ms <= now_ms]
        for token in expired_active:
            self.active.pop(token, None)

    def eval(self, script, numkeys, *args):
        keys = args[:numkeys]
        argv = args[numkeys:]

        if 'return redis.call("ZCARD", KEYS[1])' in script:
            now_ms = int(argv[0])
            self._cleanup(now_ms)
            return len(self.active)

        lock_key = keys[0]
        token = argv[0]
        owner_id = argv[1]

        if 'return "capacity_full"' in script:
            now_ms = int(argv[2])
            ttl_ms = int(argv[3])
            max_active = int(argv[4])
            client_id = str(argv[5]).strip() if len(argv) >= 6 else ""
            self._cleanup(now_ms)
            existing = self.locks.get(lock_key)
            if existing:
                if existing[0] == owner_id:
                    self.locks[lock_key] = (owner_id, now_ms + ttl_ms)
                    if client_id:
                        self.clients[token] = client_id
                    self.active[token] = now_ms + ttl_ms
                    return "admitted"
                if client_id and self.clients.get(token) == client_id:
                    self.locks[lock_key] = (owner_id, now_ms + ttl_ms)
                    self.clients[token] = client_id
                    self.active[token] = now_ms + ttl_ms
                    return "admitted"
                return "duplicate_token"
            if len(self.active) >= max_active:
                return "capacity_full"
            self.locks[lock_key] = (owner_id, now_ms + ttl_ms)
            if client_id:
                self.clients[token] = client_id
            self.active[token] = now_ms + ttl_ms
            return "admitted"

        if 'return "lost_lock"' in script:
            now_ms = int(argv[2])
            ttl_ms = int(argv[3])
            client_id = str(argv[4]).strip() if len(argv) >= 5 else ""
            self._cleanup(now_ms)
            existing = self.locks.get(lock_key)
            if not existing or existing[0] != owner_id:
                return "lost_lock"
            self.locks[lock_key] = (owner_id, now_ms + ttl_ms)
            if client_id:
                self.clients[token] = client_id
            self.active[token] = now_ms + ttl_ms
            return "ok"

        # release script
        existing = self.locks.get(lock_key)
        if existing and existing[0] == owner_id:
            self.locks.pop(lock_key, None)
            self.clients.pop(token, None)
            self.active.pop(token, None)
            return 1
        return 0


def test_occupancy_rejects_duplicate_token(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=5)
    )

    assert ctrl.acquire("INT-1", "owner-a") == "admitted"
    assert ctrl.acquire("INT-1", "owner-b") == "duplicate_token"


def test_occupancy_blocks_when_capacity_full(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=1)
    )

    assert ctrl.acquire("INT-1", "owner-a") == "admitted"
    assert ctrl.acquire("INT-2", "owner-b") == "capacity_full"


def test_occupancy_allows_reacquire_after_ttl_expired(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("INTERVIEW_OCCUPANCY_TTL_SECONDS", "1")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=1)
    )

    assert ctrl.acquire("INT-1", "owner-a") == "admitted"
    time.sleep(1.05)
    assert ctrl.acquire("INT-2", "owner-b") == "admitted"


def test_occupancy_heartbeat_and_release_require_same_owner(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=5)
    )

    assert ctrl.acquire("INT-1", "owner-a") == "admitted"
    assert ctrl.heartbeat("INT-1", "owner-b") == "lost_lock"
    assert ctrl.release("INT-1", "owner-b") is False
    assert ctrl.release("INT-1", "owner-a") is True


def test_occupancy_active_count_cleans_expired(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("INTERVIEW_OCCUPANCY_TTL_SECONDS", "1")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=5)
    )

    assert ctrl.acquire("INT-1", "owner-a") == "admitted"
    assert ctrl.active_count() == 1
    time.sleep(1.05)
    assert ctrl.active_count() == 0


def test_occupancy_allows_reconnect_takeover_for_same_client_id(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=5)
    )

    assert ctrl.acquire("INT-1", "owner-a", "client-a") == "admitted"
    assert ctrl.acquire("INT-1", "owner-b", "client-a") == "admitted"


def test_occupancy_rejects_reconnect_takeover_for_different_client_id(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setattr(occupancy.Redis, "from_url", lambda *a, **k: fake_redis)
    ctrl = occupancy.InterviewOccupancy(
        occupancy.load_occupancy_config(max_active=5)
    )

    assert ctrl.acquire("INT-1", "owner-a", "client-a") == "admitted"
    assert ctrl.acquire("INT-1", "owner-b", "client-b") == "duplicate_token"
