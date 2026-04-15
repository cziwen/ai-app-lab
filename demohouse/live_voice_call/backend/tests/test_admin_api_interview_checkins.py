import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import admin_api
import admin_login_guard
import admin_store
import stt_audio_url


class _FakeExpiryIndex:
    def ensure_ready(self):
        return True

    def schedule(self, token: str, expires_at: str):
        return True

    def remove(self, token: str):
        return True

    def due_tokens(self, now_ms=None, *, limit=200):
        return []


class _FakeRedisPipeline:
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
        return _FakeRedisPipeline(self)

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


def _setup_tmp_store(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    storage_dir = data_dir / "storage"
    audio_dir = storage_dir / "audio"
    interview_log_dir = storage_dir / "interview_logs"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(admin_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_store, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(admin_store, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(admin_store, "INTERVIEW_LOG_DIR", interview_log_dir)
    monkeypatch.setattr(admin_store, "DB_PATH", db_path)
    monkeypatch.setattr(admin_store, "INTERVIEW_EXPIRY_INDEX", _FakeExpiryIndex())


def _client_with_login(monkeypatch, tmp_path: Path) -> TestClient:
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("INTERVIEW_BASE_DOMAIN", "http://localhost:8080")
    app = admin_api.create_admin_app()
    client = TestClient(app)
    response = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert response.status_code == 200
    return client


def _setup_login_guard_env(monkeypatch, fake_redis: _FakeRedis, *, lock_start=5, lock_max=60):
    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("ADMIN_LOGIN_LOCK_START", str(lock_start))
    monkeypatch.setenv("ADMIN_LOGIN_LOCK_MAX_SECONDS", str(lock_max))
    monkeypatch.setenv("ADMIN_LOGIN_FAIL_WINDOW_SECONDS", "900")
    monkeypatch.setenv("ADMIN_LOGIN_GUARD_PREFIX", "admin:login:guard")
    monkeypatch.setattr(
        admin_login_guard.Redis,
        "from_url",
        lambda *args, **kwargs: fake_redis,
    )


def _followups_for_job(job_uid: str):
    detail = admin_store.get_job_detail(job_uid)
    assert detail is not None
    return [
        {
            "question_id": int(question["id"]),
            "max_followups": 0,
            "max_clarifies": 0,
        }
        for question in detail["questions"]
    ]


def test_create_interview_invalid_required_checkins_returns_400(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="前端工程师",
        duties="负责开发",
        requirements="熟悉 TypeScript",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目", "背景 职责 结果")],
    )

    response = client.post(
        "/api/admin/interviews",
        json={
            "candidate_name": "张三",
            "job_uid": job["job_uid"],
            "question_followups": _followups_for_job(job["job_uid"]),
            "required_checkins": ["speaker", "foo"],
        },
    )
    assert response.status_code == 400
    assert "required_checkins" in response.json().get("detail", "")


def test_update_job_endpoint_updates_text_fields(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="前端工程师",
        duties="负责开发",
        requirements="熟悉 TypeScript",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目", "背景 职责 结果")],
    )
    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None

    response = client.put(
        f"/api/admin/jobs/{job['job_uid']}",
        data={
            "name": "前端工程师(高级)",
            "duties": "负责架构与开发",
            "requirements": "熟悉 TypeScript 与工程化",
            "notes": "更新说明",
            "expected_updated_at": detail["updated_at"],
        },
    )
    assert response.status_code == 200
    payload = response.json()["job"]
    assert payload["name"] == "前端工程师(高级)"
    assert payload["question_bank_version"] == 1


def test_update_job_endpoint_returns_409_on_stale_updated_at(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="后端工程师",
        duties="负责开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目", "背景 职责 结果")],
    )
    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None

    first = client.put(
        f"/api/admin/jobs/{job['job_uid']}",
        data={
            "name": "后端工程师v2",
            "duties": "负责开发v2",
            "requirements": "熟悉 Python",
            "notes": "",
            "expected_updated_at": detail["updated_at"],
        },
    )
    assert first.status_code == 200

    second = client.put(
        f"/api/admin/jobs/{job['job_uid']}",
        data={
            "name": "后端工程师v3",
            "duties": "负责开发v3",
            "requirements": "熟悉 Python",
            "notes": "",
            "expected_updated_at": detail["updated_at"],
        },
    )
    assert second.status_code == 409
    assert "刷新后重试" in second.json().get("detail", "")


def test_delete_job_endpoint_returns_409_when_interviews_exist(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目", "背景 职责 结果")],
    )
    admin_store.create_interview(
        candidate_name="张三",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )

    response = client.delete(f"/api/admin/jobs/{job['job_uid']}")
    assert response.status_code == 409
    assert "不能删除" in response.json().get("detail", "")


def test_detail_and_public_access_return_required_checkins(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="李四",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
        required_checkins=["speaker", "screen"],
    )

    detail_response = client.get(f"/api/admin/interviews/{interview['token']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["interview"]["required_checkins"] == [
        "speaker",
        "screen",
    ]
    assert detail_response.json()["interview"]["enable_live_subtitle"] is False
    selected_questions = detail_response.json()["interview"]["selected_questions"]
    assert selected_questions
    assert "max_clarifies" in selected_questions[0]

    public_response = client.get(
        f"/api/public/interviews/{interview['token']}/access",
    )
    assert public_response.status_code == 200
    assert public_response.json()["interview"]["required_checkins"] == [
        "speaker",
        "screen",
    ]
    assert public_response.json()["interview"]["enable_live_subtitle"] is False


def test_create_interview_enable_live_subtitle_propagates(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责测试",
        requirements="熟悉测试流程",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目A", "答案A")],
    )
    create_response = client.post(
        "/api/admin/interviews",
        json={
            "candidate_name": "张三",
            "job_uid": job["job_uid"],
            "question_followups": _followups_for_job(job["job_uid"]),
            "required_checkins": ["speaker", "mic"],
            "enable_live_subtitle": True,
        },
    )
    assert create_response.status_code == 200
    token = create_response.json()["interview"]["token"]
    assert create_response.json()["interview"]["enable_live_subtitle"] is True

    detail_response = client.get(f"/api/admin/interviews/{token}")
    assert detail_response.status_code == 200
    assert detail_response.json()["interview"]["enable_live_subtitle"] is True

    public_response = client.get(f"/api/public/interviews/{token}/access")
    assert public_response.status_code == 200
    assert public_response.json()["interview"]["enable_live_subtitle"] is True


def test_public_audio_endpoint_requires_valid_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责测试",
        requirements="熟悉测试流程",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目A", "答案A")],
    )
    interview = admin_store.create_interview(
        candidate_name="张三",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]
    admin_store.persist_interview_audio(
        token,
        candidate_pcm_bytes=b"\x00\x00" * 320,
        interviewer_encoded_bytes=b"",
    )
    exp = int(time.time()) + 600
    valid_sig = stt_audio_url.sign_audio_access(
        token,
        "candidate",
        exp,
        secret="test-secret",
    )

    ok_rsp = client.get(
        f"/api/public/interviews/{token}/audio/candidate?exp={exp}&sig={valid_sig}"
    )
    assert ok_rsp.status_code == 200
    assert ok_rsp.headers["content-type"].startswith("audio/")

    bad_rsp = client.get(
        f"/api/public/interviews/{token}/audio/candidate?exp={exp}&sig=bad"
    )
    assert bad_rsp.status_code == 403


def test_public_audio_endpoint_supports_question_audio_query(monkeypatch, tmp_path):
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "test-secret")
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责测试",
        requirements="熟悉测试流程",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目A", "答案A")],
    )
    interview = admin_store.create_interview(
        candidate_name="张三",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]
    question_id = "q1"
    question_epoch = 0
    file_stub = admin_store._question_audio_file_stub(question_id, question_epoch)
    question_dir = admin_store.AUDIO_DIR / token / "canonical" / "questions"
    question_dir.mkdir(parents=True, exist_ok=True)
    question_path = question_dir / f"{file_stub}.wav"
    question_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    exp = int(time.time()) + 600
    valid_sig = stt_audio_url.sign_audio_access(
        token,
        "candidate",
        exp,
        secret="test-secret",
    )
    rsp = client.get(
        f"/api/public/interviews/{token}/audio/candidate"
        f"?exp={exp}&sig={valid_sig}&question_id={question_id}&question_epoch={question_epoch}"
    )
    assert rsp.status_code == 200
    assert rsp.headers["content-type"].startswith("audio/")


def test_create_interview_invalid_enable_live_subtitle_returns_422(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责测试",
        requirements="熟悉测试流程",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目A", "答案A")],
    )
    response = client.post(
        "/api/admin/interviews",
        json={
            "candidate_name": "张三",
            "job_uid": job["job_uid"],
            "question_followups": _followups_for_job(job["job_uid"]),
            "required_checkins": ["speaker", "mic"],
            "enable_live_subtitle": "true",
        },
    )
    assert response.status_code == 422


def test_interview_detail_includes_scorecard(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="李四",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
        required_checkins=["speaker", "mic"],
    )

    response = client.get(f"/api/admin/interviews/{interview['token']}")
    assert response.status_code == 200
    scorecard = response.json()["interview"]["scorecard"]
    assert scorecard["status"] == "pending"
    assert scorecard["question_scores"] == []


def test_create_interview_invalid_question_followups_returns_400(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责测试",
        requirements="熟悉测试流程",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目A", "答案A"), ("题目B", "答案B")],
    )
    followups = _followups_for_job(job["job_uid"])
    response = client.post(
        "/api/admin/interviews",
        json={
            "candidate_name": "张三",
            "job_uid": job["job_uid"],
            "question_followups": followups[:1],
            "required_checkins": ["speaker", "mic"],
        },
    )
    assert response.status_code == 400
    assert "question_followups" in response.json().get("detail", "")


def test_create_interview_missing_max_clarifies_returns_422(monkeypatch, tmp_path):
    client = _client_with_login(monkeypatch, tmp_path)
    job = admin_store.create_job(
        name="测试岗位",
        duties="负责测试",
        requirements="熟悉测试流程",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目A", "答案A")],
    )
    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None
    first_question_id = int(detail["questions"][0]["id"])
    response = client.post(
        "/api/admin/interviews",
        json={
            "candidate_name": "张三",
            "job_uid": job["job_uid"],
            "question_followups": [
                {
                    "question_id": first_question_id,
                    "max_followups": 1,
                }
            ],
            "required_checkins": ["speaker", "mic"],
        },
    )
    assert response.status_code == 422


def test_admin_interview_metrics_returns_active_counts(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    app = admin_api.create_admin_app(
        active_interview_metrics_provider=lambda: {
            "active_interviews": 3,
            "max_active_interviews": 5,
        }
    )
    client = TestClient(app)
    login = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert login.status_code == 200

    response = client.get("/api/admin/interviews/metrics")
    assert response.status_code == 200
    assert response.json() == {"active_interviews": 3, "max_active_interviews": 5}


def test_build_interview_link_uses_https_domain_without_trailing_slash(monkeypatch):
    monkeypatch.setenv("INTERVIEW_BASE_DOMAIN", "https://smartinterview.cn/")
    assert (
        admin_api.build_interview_link("INT-abc123")
        == "https://smartinterview.cn/check-in?token=INT-abc123"
    )


def test_build_interview_link_adds_https_scheme_when_missing(monkeypatch):
    monkeypatch.setenv("INTERVIEW_BASE_DOMAIN", "smartinterview.cn")
    assert (
        admin_api.build_interview_link("INT-abc123")
        == "https://smartinterview.cn/check-in?token=INT-abc123"
    )


def test_build_interview_link_ignores_configured_path(monkeypatch):
    monkeypatch.setenv("INTERVIEW_BASE_DOMAIN", "https://smartinterview.cn/foo")
    assert (
        admin_api.build_interview_link("INT-abc123")
        == "https://smartinterview.cn/check-in?token=INT-abc123"
    )


def test_build_interview_link_requires_domain(monkeypatch):
    monkeypatch.delenv("INTERVIEW_BASE_DOMAIN", raising=False)
    with pytest.raises(HTTPException) as exc:
        admin_api.build_interview_link("INT-abc123")
    assert exc.value.status_code == 500
    assert exc.value.detail == "INTERVIEW_BASE_DOMAIN missing"


def test_admin_login_guard_returns_429_with_retry_after(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    fake_redis = _FakeRedis()
    _setup_login_guard_env(monkeypatch, fake_redis, lock_start=5, lock_max=60)

    app = admin_api.create_admin_app()
    client = TestClient(app)
    headers = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(5):
        response = client.post(
            "/api/admin/auth/login",
            json={"username": "admin", "password": "wrong"},
            headers=headers,
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "wrong"},
        headers=headers,
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) >= 1


def test_admin_login_guard_lock_preempts_credential_check(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")

    class _AlwaysLockedGuard:
        def get_failure_counts(self, username: str, ip: str):
            return 9, 9

        def is_locked(self, username: str, ip: str):
            return True, 7

        def record_failure(self, username: str, ip: str):
            raise AssertionError("record_failure should not run while locked")

        def clear_success(self, username: str, ip: str):
            raise AssertionError("clear_success should not run while locked")

    def _unexpected_verify(*args, **kwargs):
        raise AssertionError("verify_admin_credentials should not be called")

    monkeypatch.setattr(admin_api, "verify_admin_credentials", _unexpected_verify)
    app = admin_api.create_admin_app(login_guard=_AlwaysLockedGuard())
    client = TestClient(app)

    response = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password123"},
        headers={"X-Forwarded-For": "198.51.100.8"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
