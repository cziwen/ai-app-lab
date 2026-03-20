from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

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
    assert interview["required_checkins"] == ["speaker", "mic"]

    access = admin_store.get_public_access(interview["token"])
    assert access is not None
    assert access["candidate_name"] == "张三"
    assert access["required_checkins"] == ["speaker", "mic"]

    interview_detail = admin_store.get_interview_detail(interview["token"])
    assert interview_detail is not None
    assert interview_detail["required_checkins"] == ["speaker", "mic"]
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
    assert "must_cover" in session.questions[1]["evidence"]
    assert "scoring_boundary" in session.questions[1]["evidence"]
    assert "ability_dimension" in session.questions[1]["evidence"]
    assert "best_standard" in session.questions[1]["evidence"]


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


def test_required_checkins_custom_and_empty(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="移动端工程师",
        duties="负责客户端开发",
        requirements="熟悉跨端能力",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍你的项目", "背景 职责 结果")],
    )

    interview = admin_store.create_interview(
        candidate_name="钱八",
        job_uid=job["job_uid"],
        duration_minutes=15,
        notes=None,
        required_checkins=["screen", "speaker", "screen"],
    )
    assert interview["required_checkins"] == ["speaker", "screen"]

    detail = admin_store.get_interview_detail(interview["token"])
    assert detail is not None
    assert detail["required_checkins"] == ["speaker", "screen"]

    access = admin_store.get_public_access(interview["token"])
    assert access is not None
    assert access["required_checkins"] == ["speaker", "screen"]

    empty_interview = admin_store.create_interview(
        candidate_name="周九",
        job_uid=job["job_uid"],
        duration_minutes=15,
        notes=None,
        required_checkins=[],
    )
    assert empty_interview["required_checkins"] == []


def test_required_checkins_invalid_value_raises(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="测试岗位",
        duties="职责",
        requirements="要求",
        notes=None,
        csv_filename="questions.csv",
        questions=[("题目", "答案")],
    )

    try:
        admin_store.create_interview(
            candidate_name="吴十",
            job_uid=job["job_uid"],
            duration_minutes=15,
            notes=None,
            required_checkins=["mic", "foo"],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "invalid_required_checkins"


def test_create_job_with_rubric_fields_persists_and_maps_reference_answer(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="咨询顾问",
        duties="负责咨询转化",
        requirements="熟悉医美咨询场景",
        notes=None,
        csv_filename="questions.csv",
        questions=[
            {
                "question": "你如何处理客户对价格的顾虑？",
                "ability_dimension": "沟通能力",
                "scoring_boundary": "是否兼顾客户需求与成交目标",
                "best_standard": "先共情再给分层方案并引导决策",
                "medium_standard": "能够解释但缺少场景化引导",
                "worst_standard": "仅强调价格或直接放弃沟通",
                "output_format": "评分0-5 + 摘要",
            }
        ],
    )

    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None
    assert len(detail["questions"]) == 1
    question = detail["questions"][0]
    assert question["ability_dimension"] == "沟通能力"
    assert question["scoring_boundary"] == "是否兼顾客户需求与成交目标"
    assert question["best_standard"] == "先共情再给分层方案并引导决策"
    assert question["medium_standard"] == "能够解释但缺少场景化引导"
    assert question["worst_standard"] == "仅强调价格或直接放弃沟通"
    assert question["output_format"] == "评分0-5 + 摘要"
    assert question["reference_answer"] == question["best_standard"]


def test_schema_migration_adds_question_rubric_columns(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    admin_store.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(admin_store.DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS interviews (
              token TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_questions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_uid TEXT NOT NULL,
              question TEXT NOT NULL,
              reference_answer TEXT NOT NULL,
              sort_order INTEGER NOT NULL
            );
            """
        )
        conn.commit()

    admin_store.ensure_storage()

    with admin_store.get_conn() as conn:
        question_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(job_questions)").fetchall()
        }
    assert "ability_dimension" in question_columns
    assert "scoring_boundary" in question_columns
    assert "best_standard" in question_columns
    assert "medium_standard" in question_columns
    assert "worst_standard" in question_columns
    assert "output_format" in question_columns
