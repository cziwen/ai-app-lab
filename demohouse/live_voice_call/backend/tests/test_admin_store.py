from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_calculate_question_count():
    assert admin_store.calculate_question_count(30, 10) == 5
    assert admin_store.calculate_question_count(31, 10) == 5
    assert admin_store.calculate_question_count(5, 10) == 1
    assert admin_store.calculate_question_count(15, 1) == 1


def test_create_job_and_interview(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")

    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="前端工程师",
        duties="负责前端开发",
        requirements="熟悉 TypeScript",
        notes=None,
        csv_filename="questions.csv",
        questions=[
            ("请介绍一下你最近的项目", "说明背景、你的职责、结果"),
            ("如何优化页面性能", "从指标、手段和效果说明"),
        ],
    )

    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None
    assert len(detail["questions"]) == 2

    interview = admin_store.create_interview(
        candidate_name="张三",
        job_uid=job["job_uid"],
        duration_minutes=30,
        notes="重点看项目经验",
    )
    assert interview["question_count"] == 2

    access = admin_store.get_public_access(interview["token"])
    assert access is not None
    assert access["candidate_name"] == "张三"

    interview_detail = admin_store.get_interview_detail(interview["token"])
    assert interview_detail is not None
    assert len(interview_detail["selected_questions"]) == interview["question_count"]
    assert all(
        "question" in item and item["question"] for item in interview_detail["selected_questions"]
    )


def test_interview_timeout_and_failed_after_three_interruptions(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[
            ("介绍一个项目", "背景、职责、结果"),
            ("如何定位线上问题", "现象、排查、修复"),
        ],
    )
    interview = admin_store.create_interview(
        candidate_name="李四",
        job_uid=job["job_uid"],
        duration_minutes=20,
        notes=None,
    )
    token = interview["token"]

    started = admin_store.start_interview_session(token)
    assert started is not None

    for expected_count in (1, 2, 3):
        assert admin_store.mark_interview_disconnected(token, grace_seconds=30) is True
        expired = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        with admin_store.get_conn() as conn:
            conn.execute(
                "UPDATE interviews SET reconnect_deadline_at = ? WHERE token = ?",
                (expired, token),
            )
            conn.commit()
        admin_store.resolve_interview_timeout(token)
        detail = admin_store.get_interview_detail(token)
        assert detail is not None
        assert detail["interruption_count"] == expected_count

    final_detail = admin_store.get_interview_detail(token)
    assert final_detail is not None
    assert final_detail["status"] == admin_store.INTERVIEW_STATUS_FAILED
    assert admin_store.get_public_access(token) is None
    assert admin_store.start_interview_session(token) is None


def test_reconnect_within_deadline_does_not_increment_interruptions(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="测试工程师",
        duties="负责测试",
        requirements="熟悉自动化测试",
        notes=None,
        csv_filename="questions.csv",
        questions=[("如何设计测试用例", "覆盖边界和主流程")],
    )
    interview = admin_store.create_interview(
        candidate_name="王五",
        job_uid=job["job_uid"],
        duration_minutes=10,
        notes=None,
    )
    token = interview["token"]

    assert admin_store.start_interview_session(token) is not None
    assert admin_store.mark_interview_disconnected(token, grace_seconds=30) is True
    assert admin_store.start_interview_session(token) is not None

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["interruption_count"] == 0
    assert detail["reconnect_deadline_at"] is None


def test_start_interview_session_prepends_fixed_intro_question(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="算法工程师",
        duties="负责模型研发",
        requirements="熟悉机器学习与工程实现",
        notes=None,
        csv_filename="questions.csv",
        questions=[
            ("请介绍一个你做过的项目", "背景 职责 结果"),
            ("你如何处理线上性能问题", "定位 优化 验证"),
        ],
    )
    interview = admin_store.create_interview(
        candidate_name="赵六",
        job_uid=job["job_uid"],
        duration_minutes=30,
        notes=None,
    )

    session = admin_store.start_interview_session(interview["token"])
    assert session is not None
    assert session.questions
    assert session.questions[0]["question_id"] == admin_store.FIXED_INTRO_QUESTION_ID
    assert session.questions[0]["main_question"] == admin_store.FIXED_INTRO_QUESTION_TEXT
    assert session.questions[1]["question_id"] != admin_store.FIXED_INTRO_QUESTION_ID


def test_delete_interview_removes_audio_and_log_dirs(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="运维工程师",
        duties="负责系统稳定性",
        requirements="熟悉监控告警",
        notes=None,
        csv_filename="questions.csv",
        questions=[("你如何处理服务故障", "发现 定位 恢复")],
    )
    interview = admin_store.create_interview(
        candidate_name="孙七",
        job_uid=job["job_uid"],
        duration_minutes=15,
        notes=None,
    )
    token = interview["token"]

    audio_dir = admin_store.AUDIO_DIR / token
    audio_dir.mkdir(parents=True, exist_ok=True)
    (audio_dir / "candidate.wav").write_bytes(b"fake")

    log_dir = admin_store.INTERVIEW_LOG_DIR / token
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "backend.log").write_text("test", encoding="utf-8")

    assert admin_store.delete_interview(token) is True
    assert not audio_dir.exists()
    assert not log_dir.exists()
