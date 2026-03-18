from pathlib import Path

import admin_store


def _setup_tmp_store(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    storage_dir = data_dir / "storage"
    audio_dir = storage_dir / "audio"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(admin_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_store, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(admin_store, "AUDIO_DIR", audio_dir)
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
