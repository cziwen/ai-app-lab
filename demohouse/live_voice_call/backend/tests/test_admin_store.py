from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import admin_store


class _FakeExpiryIndex:
    def __init__(self):
        self.entries = {}

    def ensure_ready(self):
        return True

    def schedule(self, token: str, expires_at: str):
        dt = datetime.fromisoformat(expires_at)
        self.entries[token] = int(dt.timestamp() * 1000)
        return True

    def remove(self, token: str):
        self.entries.pop(token, None)
        return True

    def due_tokens(self, now_ms=None, *, limit=200):
        current_ms = (
            int(datetime.now(timezone.utc).timestamp() * 1000)
            if now_ms is None
            else int(now_ms)
        )
        due = [token for token, score in self.entries.items() if score <= current_ms]
        due.sort()
        return due[: max(1, int(limit))]


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


def _followups_for_job(job_uid: str, *, max_followups: int = 0):
    detail = admin_store.get_job_detail(job_uid)
    assert detail is not None
    return [
        {"question_id": int(question["id"]), "max_followups": max_followups}
        for question in detail["questions"]
    ]


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
        notes="重点看项目经验",
        question_followups=_followups_for_job(job["job_uid"]),
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
    assert all("max_followups" in item for item in interview_detail["selected_questions"])


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
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
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
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]

    assert admin_store.start_interview_session(token) is not None
    assert admin_store.mark_interview_disconnected(token, grace_seconds=30) is True
    assert admin_store.start_interview_session(token) is not None

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["interruption_count"] == 0
    assert detail["reconnect_deadline_at"] is None


def test_start_interview_session_uses_selected_questions_only(monkeypatch, tmp_path):
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
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )

    session = admin_store.start_interview_session(interview["token"])
    assert session is not None
    assert len(session.questions) == 2
    assert all(item["question_id"] != "intro_fixed" for item in session.questions)
    assert all("max_followups" in item for item in session.questions)
    assert all("must_cover" in item["evidence"] for item in session.questions)


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
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
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
        notes=None,
        question_followups=_followups_for_job(job["job_uid"], max_followups=2),
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
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
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
            notes=None,
            question_followups=_followups_for_job(job["job_uid"]),
            required_checkins=["mic", "foo"],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "invalid_required_checkins"


def test_question_followups_must_cover_all_job_questions(monkeypatch, tmp_path):
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
        questions=[("题目1", "答案1"), ("题目2", "答案2")],
    )
    followups = _followups_for_job(job["job_uid"])
    try:
        admin_store.create_interview(
            candidate_name="候选人",
            job_uid=job["job_uid"],
            notes=None,
            question_followups=followups[:1],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "invalid_question_followups"


def test_question_followups_out_of_range_raises(monkeypatch, tmp_path):
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
        questions=[("题目1", "答案1")],
    )

    followups = _followups_for_job(job["job_uid"])
    followups[0]["max_followups"] = 4
    try:
        admin_store.create_interview(
            candidate_name="候选人",
            job_uid=job["job_uid"],
            notes=None,
            question_followups=followups,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "invalid_question_followups"


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
                "scenario": "咨询场景",
                "scoring_boundary": "是否兼顾客户需求与成交目标",
                "score_format": "评分0-5",
            }
        ],
    )

    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None
    assert len(detail["questions"]) == 1
    question = detail["questions"][0]
    assert question["scenario"] == "咨询场景"
    assert question["scoring_boundary"] == "是否兼顾客户需求与成交目标"
    assert question["score_format"] == "评分0-5"
    assert question["reference_answer"] == "是否兼顾客户需求与成交目标"


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
        interview_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(interviews)").fetchall()
        }
        scorecard_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(interview_scorecards)").fetchall()
        }
        question_score_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(interview_question_scores)"
            ).fetchall()
        }
    assert "ability_dimension" in question_columns
    assert "scenario" in question_columns
    assert "scoring_boundary" in question_columns
    assert "best_standard" in question_columns
    assert "medium_standard" in question_columns
    assert "worst_standard" in question_columns
    assert "output_format" in question_columns
    assert "score_format" in question_columns
    assert "comment_requirement" in question_columns
    assert "question_followup_limits" in interview_columns
    assert "expires_at" in interview_columns
    assert "completed_reason" in interview_columns
    assert "status" in scorecard_columns
    assert "overall_score" in scorecard_columns
    assert "total_score" in scorecard_columns
    assert "total_max_score" in scorecard_columns
    assert "error_message" in scorecard_columns
    assert "numeric_score" in question_score_columns
    assert "comment" in question_score_columns
    assert "score_format" in question_score_columns
    assert "comment_requirement" in question_score_columns
    assert "max_score" in question_score_columns
    assert "score_error" in question_score_columns


def test_expired_interview_becomes_invalid_for_access_and_start(monkeypatch, tmp_path):
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
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with admin_store.get_conn() as conn:
        conn.execute(
            "UPDATE interviews SET expires_at = ? WHERE token = ?",
            (expired, token),
        )
        conn.commit()
    assert admin_store.INTERVIEW_EXPIRY_INDEX.schedule(token, expired) is True

    assert admin_store.get_public_access(token) is None
    assert admin_store.start_interview_session(token) is None
    summary = admin_store.sweep_expired_interviews(limit=10)
    assert summary["expired"] == 1
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["status"] == admin_store.INTERVIEW_STATUS_FAILED


def test_sweep_expired_interviews_respects_batch_limit(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="批处理岗位",
        duties="批处理验证",
        requirements="熟悉测试",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍项目", "背景 职责 结果")],
    )
    tokens = []
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    for idx in range(3):
        interview = admin_store.create_interview(
            candidate_name=f"用户{idx}",
            job_uid=job["job_uid"],
            notes=None,
            question_followups=_followups_for_job(job["job_uid"]),
        )
        token = interview["token"]
        tokens.append(token)
        with admin_store.get_conn() as conn:
            conn.execute(
                "UPDATE interviews SET expires_at = ? WHERE token = ?",
                (expired, token),
            )
            conn.commit()
        admin_store.INTERVIEW_EXPIRY_INDEX.schedule(token, expired)

    first = admin_store.sweep_expired_interviews(limit=2)
    assert first["scanned"] == 2
    second = admin_store.sweep_expired_interviews(limit=2)
    assert second["expired"] == 1
    for token in tokens:
        detail = admin_store.get_interview_detail(token)
        assert detail is not None
        assert detail["status"] == admin_store.INTERVIEW_STATUS_FAILED


def test_sweep_expired_interviews_raises_when_redis_unavailable(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)

    class _UnavailableExpiryIndex:
        def due_tokens(self, now_ms=None, *, limit=200):
            return None

    monkeypatch.setattr(admin_store, "INTERVIEW_EXPIRY_INDEX", _UnavailableExpiryIndex())
    try:
        admin_store.sweep_expired_interviews(limit=10)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "interview_expiry_redis_unavailable"


def test_create_interview_fails_when_expiry_schedule_fails(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    class _FailingExpiryIndex:
        def schedule(self, token: str, expires_at: str):
            return False

        def remove(self, token: str):
            return True

    monkeypatch.setattr(admin_store, "INTERVIEW_EXPIRY_INDEX", _FailingExpiryIndex())
    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    try:
        admin_store.create_interview(
            candidate_name="测试用户",
            job_uid=job["job_uid"],
            notes=None,
            question_followups=_followups_for_job(job["job_uid"]),
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "interview_expiry_schedule_failed"


def test_mark_interview_completed_persists_completed_reason(monkeypatch, tmp_path):
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
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]

    assert admin_store.mark_interview_in_progress(token) is True
    admin_store.mark_interview_completed(
        token,
        completed_reason=admin_store.INTERVIEW_COMPLETED_REASON_HANGUP,
    )
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["status"] == admin_store.INTERVIEW_STATUS_COMPLETED
    assert detail["completed_reason"] == admin_store.INTERVIEW_COMPLETED_REASON_HANGUP


def test_interview_scorecard_success_is_persisted_and_returned(monkeypatch, tmp_path):
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
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]

    admin_store.init_interview_scorecard(token)
    admin_store.save_interview_scorecard_success(
        token,
        total_score=4.25,
        total_max_score=5.0,
        question_scores=[
            {
                "question_id": "q1",
                "sort_order": 1,
                "question": "介绍一个项目",
                "ability_dimension": "项目管理",
                "score_format": "评分0-5",
                "comment_requirement": "摘要 + 改进建议",
                "aggregated_answer": "我负责拆解目标并推动上线。",
                "max_score": 5.0,
                "score_error": "",
                "numeric_score": 4.25,
                "comment": "回答覆盖较完整。",
            }
        ],
    )

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    scorecard = detail["scorecard"]
    assert scorecard is not None
    assert scorecard["status"] == admin_store.SCORECARD_STATUS_COMPLETED
    assert scorecard["overall_score"] is None
    assert scorecard["total_score"] == 4.25
    assert scorecard["total_max_score"] == 5.0
    assert len(scorecard["question_scores"]) == 1
    assert scorecard["question_scores"][0]["score_format"] == "评分0-5"
    assert scorecard["question_scores"][0]["comment_requirement"] == "摘要 + 改进建议"
    assert scorecard["question_scores"][0]["max_score"] == 5.0
    assert scorecard["question_scores"][0]["score_error"] == ""
    assert scorecard["question_scores"][0]["comment"] == "回答覆盖较完整。"


def test_interview_scorecard_failed_clears_question_scores(monkeypatch, tmp_path):
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
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]

    admin_store.init_interview_scorecard(token)
    admin_store.save_interview_scorecard_success(
        token,
        total_score=3.0,
        total_max_score=5.0,
        question_scores=[
            {
                "question_id": "q1",
                "sort_order": 1,
                "question": "介绍一个项目",
                "ability_dimension": "",
                "score_format": "",
                "comment_requirement": "",
                "aggregated_answer": "回答",
                "max_score": 5.0,
                "score_error": "",
                "numeric_score": 3.0,
                "comment": "中等。",
            }
        ],
    )
    admin_store.save_interview_scorecard_failed(token, "mock scoring error")

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    scorecard = detail["scorecard"]
    assert scorecard is not None
    assert scorecard["status"] == admin_store.SCORECARD_STATUS_FAILED
    assert scorecard["error_message"] == "mock scoring error"
    assert scorecard["question_scores"] == []
