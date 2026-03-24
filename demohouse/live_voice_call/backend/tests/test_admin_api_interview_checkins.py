from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import admin_api
import admin_store


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


def _client_with_login(monkeypatch, tmp_path: Path) -> TestClient:
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    app = admin_api.create_admin_app()
    client = TestClient(app)
    response = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "password123"},
    )
    assert response.status_code == 200
    return client


def _followups_for_job(job_uid: str):
    detail = admin_store.get_job_detail(job_uid)
    assert detail is not None
    return [
        {"question_id": int(question["id"]), "max_followups": 0}
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

    public_response = client.get(
        f"/api/public/interviews/{interview['token']}/access",
    )
    assert public_response.status_code == 200
    assert public_response.json()["interview"]["required_checkins"] == [
        "speaker",
        "screen",
    ]


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
