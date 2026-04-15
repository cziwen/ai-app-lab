from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

import admin_store
from auc_stt_client import AucSTTResult


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


def _followups_for_job(
    job_uid: str,
    *,
    max_followups: int = 0,
    max_clarifies: int = 0,
):
    detail = admin_store.get_job_detail(job_uid)
    assert detail is not None
    return [
        {
            "question_id": int(question["id"]),
            "max_followups": max_followups,
            "max_clarifies": max_clarifies,
        }
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
    assert interview["enable_live_subtitle"] is False

    access = admin_store.get_public_access(interview["token"])
    assert access is not None
    assert access["candidate_name"] == "张三"
    assert access["required_checkins"] == ["speaker", "mic"]
    assert access["enable_live_subtitle"] is False

    interview_detail = admin_store.get_interview_detail(interview["token"])
    assert interview_detail is not None
    assert interview_detail["required_checkins"] == ["speaker", "mic"]
    assert interview_detail["enable_live_subtitle"] is False
    assert len(interview_detail["selected_questions"]) == interview["question_count"]
    assert all(
        "question" in item and item["question"] for item in interview_detail["selected_questions"]
    )
    assert all("max_followups" in item for item in interview_detail["selected_questions"])
    assert all("max_clarifies" in item for item in interview_detail["selected_questions"])


def test_update_job_text_only_keeps_question_bank_version(monkeypatch, tmp_path):
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
        questions=[("介绍项目", "背景 职责 结果")],
    )
    before = admin_store.get_job_detail(job["job_uid"])
    assert before is not None
    assert before["question_bank_version"] == 1
    assert len(before["questions"]) == 1

    updated = admin_store.update_job(
        job_uid=job["job_uid"],
        name="前端工程师(高级)",
        duties="负责前端架构与开发",
        requirements="熟悉 TypeScript 与工程化",
        notes="新增职责说明",
        expected_updated_at=before["updated_at"],
    )
    assert updated["question_bank_version"] == 1
    assert updated["name"] == "前端工程师(高级)"
    assert len(updated["questions"]) == 1


def test_update_job_with_csv_creates_new_bank_version(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="后端工程师",
        duties="负责后端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("旧题目A", "旧答案A"), ("旧题目B", "旧答案B")],
    )
    before = admin_store.get_job_detail(job["job_uid"])
    assert before is not None
    assert before["question_bank_version"] == 1

    updated = admin_store.update_job(
        job_uid=job["job_uid"],
        name="后端工程师",
        duties="负责后端开发",
        requirements="熟悉 Python",
        notes=None,
        expected_updated_at=before["updated_at"],
        csv_filename="questions_v2.csv",
        questions=[("新题目A", "新答案A")],
    )
    assert updated["question_bank_version"] == 2
    assert [item["question"] for item in updated["questions"]] == ["新题目A"]

    with admin_store.get_conn() as conn:
        v1_count = conn.execute(
            "SELECT COUNT(*) AS c FROM job_questions WHERE job_uid = ? AND bank_version = 1",
            (job["job_uid"],),
        ).fetchone()["c"]
        v2_count = conn.execute(
            "SELECT COUNT(*) AS c FROM job_questions WHERE job_uid = ? AND bank_version = 2",
            (job["job_uid"],),
        ).fetchone()["c"]
    assert int(v1_count) == 2
    assert int(v2_count) == 1


def test_create_interview_binds_current_bank_version(monkeypatch, tmp_path):
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
        questions=[("旧题库问题", "旧题库答案")],
    )
    interview_v1 = admin_store.create_interview(
        candidate_name="候选人A",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )

    detail_before_update = admin_store.get_job_detail(job["job_uid"])
    assert detail_before_update is not None
    admin_store.update_job(
        job_uid=job["job_uid"],
        name=detail_before_update["name"],
        duties=detail_before_update["duties"],
        requirements=detail_before_update["requirements"],
        notes=detail_before_update["notes"],
        expected_updated_at=detail_before_update["updated_at"],
        csv_filename="questions_v2.csv",
        questions=[("新题库问题", "新题库答案")],
    )

    interview_v2 = admin_store.create_interview(
        candidate_name="候选人B",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )

    v1_detail = admin_store.get_interview_detail(interview_v1["token"])
    v2_detail = admin_store.get_interview_detail(interview_v2["token"])
    assert v1_detail is not None
    assert v2_detail is not None
    assert v1_detail["question_bank_version"] == 1
    assert v2_detail["question_bank_version"] == 2
    assert v1_detail["selected_questions"][0]["question"] == "旧题库问题"
    assert v2_detail["selected_questions"][0]["question"] == "新题库问题"


def test_historical_interview_uses_old_bank_after_job_update(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="算法工程师",
        duties="负责模型研发",
        requirements="熟悉机器学习",
        notes=None,
        csv_filename="questions.csv",
        questions=[("旧问题", "旧答案")],
    )
    interview = admin_store.create_interview(
        candidate_name="候选人A",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    before = admin_store.get_job_detail(job["job_uid"])
    assert before is not None

    admin_store.update_job(
        job_uid=job["job_uid"],
        name=before["name"],
        duties=before["duties"],
        requirements=before["requirements"],
        notes=before["notes"],
        expected_updated_at=before["updated_at"],
        csv_filename="questions_v2.csv",
        questions=[("新问题", "新答案")],
    )

    detail = admin_store.get_interview_detail(interview["token"])
    session = admin_store.start_interview_session(interview["token"])
    assert detail is not None
    assert session is not None
    assert detail["selected_questions"][0]["question"] == "旧问题"
    assert session.questions[0]["main_question"] == "旧问题"


def test_delete_job_returns_409_when_interviews_exist(monkeypatch, tmp_path):
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
    admin_store.create_interview(
        candidate_name="候选人",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )

    try:
        admin_store.delete_job_cascade(job["job_uid"])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "job_has_interviews"

    assert admin_store.get_job_detail(job["job_uid"]) is not None


def test_update_job_returns_409_on_stale_expected_updated_at(monkeypatch, tmp_path):
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
    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None

    admin_store.update_job(
        job_uid=job["job_uid"],
        name="测试岗位v2",
        duties="职责v2",
        requirements="要求v2",
        notes=None,
        expected_updated_at=detail["updated_at"],
    )
    try:
        admin_store.update_job(
            job_uid=job["job_uid"],
            name="测试岗位v3",
            duties="职责v3",
            requirements="要求v3",
            notes=None,
            expected_updated_at=detail["updated_at"],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "job_conflict"


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
    assert admin_store.mark_interview_in_progress(token) is True

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["interruption_count"] == 0
    assert detail["reconnect_deadline_at"] is None


def test_start_interview_session_does_not_clear_reconnect_deadline(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="SRE 工程师",
        duties="保障系统稳定性",
        requirements="熟悉故障排查",
        notes=None,
        csv_filename="questions.csv",
        questions=[("你如何处理故障升级", "定位 处置 复盘")],
    )
    interview = admin_store.create_interview(
        candidate_name="赵七",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]

    assert admin_store.start_interview_session(token) is not None
    assert admin_store.mark_interview_disconnected(token, grace_seconds=30) is True
    assert admin_store.start_interview_session(token) is not None

    detail_before_mark = admin_store.get_interview_detail(token)
    assert detail_before_mark is not None
    assert detail_before_mark["reconnect_deadline_at"] is not None

    assert admin_store.mark_interview_in_progress(token) is True
    detail_after_mark = admin_store.get_interview_detail(token)
    assert detail_after_mark is not None
    assert detail_after_mark["reconnect_deadline_at"] is None


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
    assert all("max_clarifies" in item for item in session.questions)
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


def test_enable_live_subtitle_persisted_in_detail_and_access(monkeypatch, tmp_path):
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
    interview = admin_store.create_interview(
        candidate_name="王五",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
        enable_live_subtitle=True,
    )
    assert interview["enable_live_subtitle"] is True

    detail = admin_store.get_interview_detail(interview["token"])
    assert detail is not None
    assert detail["enable_live_subtitle"] is True

    access = admin_store.get_public_access(interview["token"])
    assert access is not None
    assert access["enable_live_subtitle"] is True


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


def test_question_clarifies_out_of_range_raises(monkeypatch, tmp_path):
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
    followups[0]["max_clarifies"] = 4
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


def test_create_interview_without_followups_defaults_clarifies_to_one(monkeypatch, tmp_path):
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
    interview = admin_store.create_interview(
        candidate_name="候选人",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=None,
    )
    assert all(item["max_clarifies"] == 1 for item in interview["question_followups"])

    detail = admin_store.get_interview_detail(interview["token"])
    assert detail is not None
    assert all(item["max_clarifies"] == 1 for item in detail["selected_questions"])

    session = admin_store.start_interview_session(interview["token"])
    assert session is not None
    assert all(int(item["max_clarifies"]) == 1 for item in session.questions)


def test_missing_question_clarify_limits_defaults_to_one(monkeypatch, tmp_path):
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
    interview = admin_store.create_interview(
        candidate_name="候选人",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"], max_clarifies=0),
    )

    with admin_store.get_conn() as conn:
        conn.execute(
            "UPDATE interviews SET question_clarify_limits = ? WHERE token = ?",
            ("{}", interview["token"]),
        )
        conn.commit()

    detail = admin_store.get_interview_detail(interview["token"])
    assert detail is not None
    assert all(item["max_clarifies"] == 1 for item in detail["selected_questions"])

    session = admin_store.start_interview_session(interview["token"])
    assert session is not None
    assert all(int(item["max_clarifies"]) == 1 for item in session.questions)


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
        job_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
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
    assert "question_bank_version" in job_columns
    assert "ability_dimension" in question_columns
    assert "scenario" in question_columns
    assert "scoring_boundary" in question_columns
    assert "best_standard" in question_columns
    assert "medium_standard" in question_columns
    assert "worst_standard" in question_columns
    assert "output_format" in question_columns
    assert "score_format" in question_columns
    assert "comment_requirement" in question_columns
    assert "bank_version" in question_columns
    assert "question_followup_limits" in interview_columns
    assert "question_clarify_limits" in interview_columns
    assert "expires_at" in interview_columns
    assert "completed_reason" in interview_columns
    assert "question_bank_version" in interview_columns
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


def test_mark_interview_failed_sets_terminal_status_without_downgrading_completed(
    monkeypatch, tmp_path
):
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
    admin_store.mark_interview_failed(token)
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["status"] == admin_store.INTERVIEW_STATUS_FAILED

    admin_store.mark_interview_completed(
        token,
        completed_reason=admin_store.INTERVIEW_COMPLETED_REASON_NORMAL_END,
    )
    detail_after_completed = admin_store.get_interview_detail(token)
    assert detail_after_completed is not None
    assert detail_after_completed["status"] == admin_store.INTERVIEW_STATUS_COMPLETED

    admin_store.mark_interview_failed(token)
    detail_after_failed_retry = admin_store.get_interview_detail(token)
    assert detail_after_failed_retry is not None
    assert detail_after_failed_retry["status"] == admin_store.INTERVIEW_STATUS_COMPLETED


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


def test_finalize_canonical_artifacts_merges_attempts_and_preserves_history(
    monkeypatch, tmp_path
):
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

    attempt_1 = "attempt-1"
    attempt_2 = "attempt-2"
    assert (
        admin_store.start_interview_attempt(
            token,
            attempt_1,
            client_id="cid-1",
            owner_id="owner-1",
        )
        == 1
    )
    assert (
        admin_store.start_interview_attempt(
            token,
            attempt_2,
            client_id="cid-1",
            owner_id="owner-2",
        )
        == 2
    )

    admin_store.save_interview_turn_events(
        token,
        attempt_1,
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我负责订单系统重构。",
                "question_id": "q1",
                "question_index": 1,
            },
        ],
    )
    admin_store.save_interview_turn_events(
        token,
        attempt_2,
        [
            {
                "seq_no": 1,
                "role": "candidate",
                "text": "补充说明：我还负责上线压测与回滚预案。",
                "question_id": "q1",
                "question_index": 1,
            }
        ],
    )
    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 2,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {
                        "role": "interviewer",
                        "content": "第一题，请你介绍一个项目",
                        "created_at": admin_store.utc_now_iso(),
                    },
                    {
                        "role": "candidate",
                        "content": "我负责订单系统重构。",
                        "created_at": admin_store.utc_now_iso(),
                    },
                    {
                        "role": "candidate",
                        "content": "补充说明：我还负责上线压测与回滚预案。",
                        "created_at": admin_store.utc_now_iso(),
                    },
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            attempt_2,
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id=attempt_1,
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id=attempt_2,
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        attempt_1,
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        attempt_2,
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["attempt_count"] == 2
    assert finalized["score_inputs"]

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["attempt_count"] == 2
    assert detail["canonical_status"] == admin_store.INTERVIEW_CANONICAL_STATUS_READY
    turn_texts = [item["content"] for item in detail["turns"]]
    assert "我负责订单系统重构。" in turn_texts
    assert "补充说明：我还负责上线压测与回滚预案。" in turn_texts
    assert detail["candidate_audio_path"] is not None
    assert "/canonical/" in detail["candidate_audio_path"]


def test_finalize_canonical_artifacts_prefers_later_attempt_for_overlap(
    monkeypatch, tmp_path
):
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

    assert admin_store.start_interview_attempt(token, "attempt-old", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-new", owner_id="owner-2") == 2

    old_checkpoint = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "请介绍一个项目", "created_at": admin_store.utc_now_iso()},
                    {"role": "candidate", "content": "我负责开发", "created_at": admin_store.utc_now_iso()},
                ],
            }
        ],
    }
    new_checkpoint = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 1,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "请介绍一个项目", "created_at": admin_store.utc_now_iso()},
                    {"role": "candidate", "content": "我 负责开发", "created_at": admin_store.utc_now_iso()},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-old",
            1,
            old_checkpoint,
            source="flush",
        )
        is True
    )
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-new",
            1,
            new_checkpoint,
            source="flush",
        )
        is True
    )

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-old",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-new",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    candidate_turns = [item["content"] for item in detail["turns"] if item["role"] == "candidate"]
    assert candidate_turns.count("我负责开发") == 0
    assert candidate_turns.count("我 负责开发") == 1


def test_finalize_canonical_artifacts_fails_without_candidate_turns(
    monkeypatch, tmp_path
):
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is False
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["canonical_status"] == admin_store.INTERVIEW_CANONICAL_STATUS_FAILED
    assert "checkpoint_missing" in detail["consistency_flags"]


def test_finalize_canonical_artifacts_fails_when_question_has_no_candidate_answer(
    monkeypatch, tmp_path
):
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 0,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {
                        "role": "interviewer",
                        "content": "第一题，请你介绍一个项目",
                        "created_at": admin_store.utc_now_iso(),
                    }
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-1",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is False
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["canonical_status"] == admin_store.INTERVIEW_CANONICAL_STATUS_FAILED
    assert "missing_candidate_answer" in detail["consistency_flags"]


def test_finalize_canonical_artifacts_uses_turn_events_fallback_for_hangup(
    monkeypatch, tmp_path
):
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
    assert admin_store.start_interview_attempt(token, "attempt-fallback", owner_id="owner-1") == 1

    admin_store.save_interview_turn_events(
        token,
        "attempt-fallback",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我负责订单系统重构。",
                "question_id": "q1",
                "question_index": 1,
            },
        ],
    )

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-fallback",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )

    finalized = admin_store.finalize_canonical_artifacts(
        token,
        completed_reason=admin_store.INTERVIEW_COMPLETED_REASON_HANGUP,
    )
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_TURN_EVENTS_FALLBACK
    assert finalized["score_inputs"]
    assert "checkpoint_missing" in finalized["consistency_flags"]
    assert "checkpoint_fallback_used" in finalized["consistency_flags"]
    assert "partial_scoring" in finalized["consistency_flags"]


def test_finalize_canonical_artifacts_partial_mode_allows_no_valid_answers(
    monkeypatch, tmp_path
):
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
    assert admin_store.start_interview_attempt(token, "attempt-partial-empty", owner_id="owner-1") == 1

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 0,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {
                        "role": "interviewer",
                        "content": "第一题，请你介绍一个项目",
                        "created_at": admin_store.utc_now_iso(),
                    }
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-partial-empty",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-partial-empty",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )

    finalized = admin_store.finalize_canonical_artifacts(
        token,
        completed_reason=admin_store.INTERVIEW_COMPLETED_REASON_HANGUP,
    )
    assert finalized["ok"] is True
    assert finalized["score_inputs"] == []
    assert len(finalized["unanswered_zero_items"]) == 1
    assert finalized["unanswered_zero_items"][0]["question_id"] == "scene-1"
    assert "missing_candidate_answer" in finalized["consistency_flags"]
    assert "unanswered_questions_skipped" in finalized["consistency_flags"]
    assert "no_valid_answers_for_partial_scoring" in finalized["consistency_flags"]
    assert "unanswered_questions_scored_zero" in finalized["consistency_flags"]
    assert "partial_scoring" in finalized["consistency_flags"]


def test_finalize_canonical_artifacts_prefers_checkpoint_and_discards_rewound_turns(
    monkeypatch, tmp_path
):
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-2", owner_id="owner-2") == 2

    # stale turns from old attempt should be discarded once checkpoint source is used.
    admin_store.save_interview_turn_events(
        token,
        "attempt-1",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "旧轮次回答，应该被回滚丢弃",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    admin_store.save_interview_turn_events(
        token,
        "attempt-2",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 1,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "新轮次有效回答",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 1,
            },
        ],
    )

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 1,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {
                        "role": "interviewer",
                        "content": "第一题，请你介绍一个项目",
                        "created_at": admin_store.utc_now_iso(),
                    },
                    {
                        "role": "candidate",
                        "content": "新轮次有效回答",
                        "created_at": admin_store.utc_now_iso(),
                    },
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-2",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-2",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        "attempt-2",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert finalized["discarded_turn_count"] >= 1

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    candidate_turns = [item["content"] for item in detail["turns"] if item["role"] == "candidate"]
    assert "新轮次有效回答" in candidate_turns
    assert "旧轮次回答，应该被回滚丢弃" not in candidate_turns


def test_finalize_canonical_artifacts_marks_echo_risk_flag(monkeypatch, tmp_path):
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
    assert admin_store.start_interview_attempt(token, "attempt-echo", owner_id="owner-1") == 1

    now_iso = admin_store.utc_now_iso()
    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "你怎么看加班这件事", "created_at": now_iso},
                    {"role": "candidate", "content": "你怎么看加班这件事", "created_at": now_iso},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-echo",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )

    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    pcm = (b"\x01\x00\x02\x00") * 800
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-echo",
        candidate_pcm_bytes=pcm,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-echo",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert "candidate_echo_suspected" in finalized["echo_risk_flags"]

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert "candidate_echo_suspected" in detail["echo_risk_flags"]


def test_finalize_canonical_artifacts_pins_audio_to_checkpoint_attempt(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-2", owner_id="owner-2") == 2

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 1,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "新轮次有效回答"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-2",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )

    attempt1_candidate_pcm = (b"\x01\x00\x02\x00") * 400
    attempt2_candidate_pcm = (b"\x01\x00\x02\x00") * 900
    interviewer_raw_1 = b"\x01\x02\x03\x04" * 200
    interviewer_raw_2 = b"\x05\x06\x07\x08" * 300
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=attempt1_candidate_pcm,
        interviewer_encoded_bytes=interviewer_raw_1,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-2",
        candidate_pcm_bytes=attempt2_candidate_pcm,
        interviewer_encoded_bytes=interviewer_raw_2,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        "attempt-2",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert "audio_attempt_pinned_to_checkpoint" in finalized["consistency_flags"]
    assert "audio_attempt_fallback_used" not in finalized["consistency_flags"]

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    canonical_candidate_path = Path(str(detail["candidate_audio_path"] or ""))
    assert canonical_candidate_path.exists()
    with admin_store.get_conn() as conn:
        attempt2_candidate_row = conn.execute(
            """
            SELECT file_path
            FROM interview_audio_segments s
            JOIN interview_attempts a ON a.attempt_id = s.attempt_id
            WHERE s.interview_token = ?
              AND s.track = 'candidate'
              AND a.attempt_seq = 2
            ORDER BY s.segment_seq ASC, s.id ASC
            LIMIT 1
            """,
            (token,),
        ).fetchone()
    assert attempt2_candidate_row is not None
    attempt2_candidate_path = Path(str(attempt2_candidate_row["file_path"] or ""))
    assert attempt2_candidate_path.exists()
    assert canonical_candidate_path.read_bytes() == attempt2_candidate_path.read_bytes()


def test_finalize_canonical_artifacts_falls_back_when_checkpoint_attempt_audio_incomplete(
    monkeypatch, tmp_path
):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-2", owner_id="owner-2") == 2

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 1,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "新轮次有效回答"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-2",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )

    # attempt-1 has both tracks, attempt-2 intentionally misses interviewer track.
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 400,
        interviewer_encoded_bytes=b"\x01\x02\x03\x04" * 200,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-2",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 900,
        interviewer_encoded_bytes=b"",
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        "attempt-2",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert "audio_attempt_fallback_used" in finalized["consistency_flags"]
    assert "audio_attempt_pinned_to_checkpoint" not in finalized["consistency_flags"]


def test_finalize_canonical_artifacts_turn_events_fallback_does_not_set_checkpoint_audio_flags(
    monkeypatch, tmp_path
):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
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
    assert admin_store.start_interview_attempt(token, "attempt-fallback", owner_id="owner-1") == 1

    admin_store.save_interview_turn_events(
        token,
        "attempt-fallback",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我负责订单系统重构。",
                "question_id": "q1",
                "question_index": 1,
            },
        ],
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-fallback",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 500,
        interviewer_encoded_bytes=b"\x01\x02\x03\x04" * 200,
    )
    admin_store.close_interview_attempt(
        "attempt-fallback",
        status="completed",
        close_source="client_ws",
    )

    finalized = admin_store.finalize_canonical_artifacts(
        token,
        completed_reason=admin_store.INTERVIEW_COMPLETED_REASON_HANGUP,
    )
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_TURN_EVENTS_FALLBACK
    assert "audio_attempt_pinned_to_checkpoint" not in finalized["consistency_flags"]
    assert "audio_attempt_fallback_used" not in finalized["consistency_flags"]


def test_finalize_canonical_artifacts_reconciles_checkpoint_tail_turns(monkeypatch, tmp_path):
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
    assert admin_store.start_interview_attempt(token, "attempt-tail", owner_id="owner-1") == 1

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "我负责订单系统重构。"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-tail",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    admin_store.save_interview_turn_events(
        token,
        "attempt-tail",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我负责订单系统重构。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 3,
                "role": "candidate",
                "text": "补充：我还负责上线压测和回滚预案。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-tail",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-tail",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert "checkpoint_tail_reconciled" in finalized["consistency_flags"]
    assert any(
        "补充：我还负责上线压测和回滚预案。"
        in str(item.get("aggregated_answer", "") or "")
        for item in finalized["score_inputs"]
    )
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert any(
        turn.get("content") == "补充：我还负责上线压测和回滚预案。"
        for turn in detail["turns"]
    )


def test_finalize_canonical_artifacts_checkpoint_tail_reconcile_noop(monkeypatch, tmp_path):
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
    assert admin_store.start_interview_attempt(token, "attempt-noop", owner_id="owner-1") == 1

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "我负责订单系统重构。"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-noop",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    admin_store.save_interview_turn_events(
        token,
        "attempt-noop",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我负责订单系统重构。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-noop",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-noop",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert "checkpoint_tail_reconcile_noop" in finalized["consistency_flags"]
    assert "checkpoint_tail_reconciled" not in finalized["consistency_flags"]


def test_finalize_canonical_artifacts_infers_audio_attempts_from_turns(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-2", owner_id="owner-2") == 2

    admin_store.save_interview_turn_events(
        token,
        "attempt-1",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我先按团队流程走，再提优化建议。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    admin_store.save_interview_turn_events(
        token,
        "attempt-2",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "前两周先跟跑，之后再做小步优化。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 2,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "我先按团队流程走，再提优化建议。"},
                    {"role": "candidate", "content": "前两周先跟跑，之后再做小步优化。"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-2",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    interviewer_1 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"A" * 64
    interviewer_2 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"B" * 64
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 300,
        interviewer_encoded_bytes=interviewer_1,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-2",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 500,
        interviewer_encoded_bytes=interviewer_2,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        "attempt-2",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert "audio_attempts_inferred_from_turns" in finalized["consistency_flags"]
    assert "audio_attempt_pinned_to_checkpoint" not in finalized["consistency_flags"]

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    canonical_candidate = Path(str(detail["candidate_audio_path"] or ""))
    canonical_interviewer = Path(str(detail["interviewer_audio_path"] or ""))
    assert canonical_candidate.exists()
    assert canonical_interviewer.exists()
    with admin_store.get_conn() as conn:
        attempt_rows = conn.execute(
            """
            SELECT a.attempt_seq, s.track, s.file_path
            FROM interview_audio_segments s
            JOIN interview_attempts a ON a.attempt_id = s.attempt_id
            WHERE s.interview_token = ?
            ORDER BY a.attempt_seq ASC, s.segment_seq ASC, s.id ASC
            """,
            (token,),
        ).fetchall()
    attempt1_candidate = Path(
        str(
            next(
                row["file_path"]
                for row in attempt_rows
                if int(row["attempt_seq"] or 0) == 1 and str(row["track"]) == "candidate"
            )
        )
    )
    attempt2_candidate = Path(
        str(
            next(
                row["file_path"]
                for row in attempt_rows
                if int(row["attempt_seq"] or 0) == 2 and str(row["track"]) == "candidate"
            )
        )
    )
    attempt1_interviewer = Path(
        str(
            next(
                row["file_path"]
                for row in attempt_rows
                if int(row["attempt_seq"] or 0) == 1 and str(row["track"]) == "interviewer"
            )
        )
    )
    attempt2_interviewer = Path(
        str(
            next(
                row["file_path"]
                for row in attempt_rows
                if int(row["attempt_seq"] or 0) == 2 and str(row["track"]) == "interviewer"
            )
        )
    )
    assert len(canonical_candidate.read_bytes()) > len(attempt1_candidate.read_bytes())
    assert len(canonical_candidate.read_bytes()) > len(attempt2_candidate.read_bytes())
    assert canonical_interviewer.read_bytes() == (
        attempt1_interviewer.read_bytes() + attempt2_interviewer.read_bytes()
    )


def test_finalize_canonical_artifacts_inferred_audio_fallbacks_when_attempt_incomplete(
    monkeypatch, tmp_path
):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-2", owner_id="owner-2") == 2

    admin_store.save_interview_turn_events(
        token,
        "attempt-1",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我先按团队流程走，再提优化建议。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    admin_store.save_interview_turn_events(
        token,
        "attempt-2",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "前两周先跟跑，之后再做小步优化。",
                "question_id": "q1",
                "question_index": 1,
                "question_epoch": 0,
            },
        ],
    )
    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 2,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "我先按团队流程走，再提优化建议。"},
                    {"role": "candidate", "content": "前两周先跟跑，之后再做小步优化。"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-2",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    interviewer_1 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"A" * 64
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 300,
        interviewer_encoded_bytes=interviewer_1,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-2",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 500,
        interviewer_encoded_bytes=b"",
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        "attempt-2",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert "audio_attempt_fallback_used" in finalized["consistency_flags"]
    assert "audio_attempts_inferred_from_turns" not in finalized["consistency_flags"]


def test_finalize_canonical_artifacts_cutline_switches_source(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", datetime.now(timezone.utc).isoformat())
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    old_interview = admin_store.create_interview(
        candidate_name="旧面试",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    old_token = old_interview["token"]
    with admin_store.get_conn() as conn:
        old_time = "2000-01-01T00:00:00+00:00"
        conn.execute(
            "UPDATE interviews SET created_at = ?, updated_at = ? WHERE token = ?",
            (old_time, old_time, old_token),
        )
        conn.commit()

    new_interview = admin_store.create_interview(
        candidate_name="新面试",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    new_token = new_interview["token"]

    def _seed_one_attempt(
        token: str,
        attempt_id: str,
        *,
        event_candidate: str,
        checkpoint_candidate: str,
    ):
        assert admin_store.mark_interview_in_progress(token) is True
        assert admin_store.start_interview_attempt(token, attempt_id, owner_id="owner-1") == 1
        admin_store.save_interview_turn_events(
            token,
            attempt_id,
            [
                {
                    "seq_no": 1,
                    "role": "interviewer",
                    "text": "第一题，请你介绍一个项目",
                    "question_id": "q1",
                    "question_index": 1,
                },
                {
                    "seq_no": 2,
                    "role": "candidate",
                    "text": event_candidate,
                    "question_id": "q1",
                    "question_index": 1,
                },
            ],
        )
        checkpoint_payload = {
            "version": 1,
            "state": "WAIT_ANSWER",
            "current_question_index": 0,
            "total_candidate_turns": 1,
            "latest_follow_up": "",
            "latest_clarify": "",
            "questions": [
                {
                    "question_id": "q1",
                    "status": "in_progress",
                    "question_epoch": 0,
                    "follow_up_count": 0,
                    "clarify_count": 0,
                    "coverage_score": 0.0,
                    "turns": [
                        {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                        {"role": "candidate", "content": checkpoint_candidate},
                    ],
                }
            ],
        }
        assert (
            admin_store.save_interview_checkpoint_journal(
                token,
                attempt_id,
                1,
                checkpoint_payload,
                source="flush",
            )
            is True
        )
        interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
        admin_store.persist_interview_audio(
            token=token,
            attempt_id=attempt_id,
            candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
            interviewer_encoded_bytes=interviewer_mp3_like,
        )
        admin_store.close_interview_attempt(
            attempt_id,
            status="completed",
            close_source="normal_end",
        )

    _seed_one_attempt(
        old_token,
        "attempt-old",
        event_candidate="旧事件候选人回答",
        checkpoint_candidate="旧checkpoint候选人回答",
    )
    _seed_one_attempt(
        new_token,
        "attempt-new",
        event_candidate="新事件候选人回答",
        checkpoint_candidate="新checkpoint候选人回答",
    )

    old_finalized = admin_store.finalize_canonical_artifacts(old_token)
    new_finalized = admin_store.finalize_canonical_artifacts(new_token)
    assert old_finalized["ok"] is True
    assert new_finalized["ok"] is True
    assert old_finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_CHECKPOINT
    assert new_finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_TURN_EVENTS

    old_detail = admin_store.get_interview_detail(old_token)
    new_detail = admin_store.get_interview_detail(new_token)
    assert old_detail is not None
    assert new_detail is not None
    old_texts = [item["content"] for item in old_detail["turns"]]
    new_texts = [item["content"] for item in new_detail["turns"]]
    assert "旧checkpoint候选人回答" in old_texts
    assert "新事件候选人回答" in new_texts
    assert "新checkpoint候选人回答" not in new_texts


def test_finalize_canonical_artifacts_events_only_keeps_wrap_up_at_end(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1
    assert admin_store.start_interview_attempt(token, "attempt-2", owner_id="owner-2") == 2

    admin_store.save_interview_turn_events(
        token,
        "attempt-1",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "你好，欢迎参加面试。",
                "question_id": "",
                "question_index": 0,
                "event_kind": "intro",
            },
            {
                "seq_no": 2,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
            },
            {
                "seq_no": 3,
                "role": "candidate",
                "text": "我负责订单系统重构。",
                "question_id": "q1",
                "question_index": 1,
            },
        ],
    )
    admin_store.save_interview_turn_events(
        token,
        "attempt-2",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "好的，面试到这里结束。感谢你的时间，我们会尽快给出反馈。",
                "question_id": "",
                "question_index": 0,
                "event_kind": "wrap_up",
            }
        ],
    )
    checkpoint_payload = {
        "version": 1,
        "state": "DONE",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "completed",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "我负责订单系统重构。"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-2",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-2",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 200,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="in_progress",
        close_source="client_ws",
    )
    admin_store.close_interview_attempt(
        "attempt-2",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_TURN_EVENTS
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    turn_texts = [item["content"] for item in detail["turns"]]
    assert turn_texts[0] == "你好，欢迎参加面试。"
    assert turn_texts[-1] == "好的，面试到这里结束。感谢你的时间，我们会尽快给出反馈。"


def test_finalize_canonical_artifacts_events_only_ignores_checkpoint_wording(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1

    original_question = "你以前在大公司、年轻客群或者某某行业干得不错，可咱们这边主要是服务中老年顾客，阿姨们挺挑的。我有点担心你会不会不习惯，或者觉得是不是有点屈才了，你怎么看？"
    rewritten_question = "嗯，我们现在来聊聊新的工作场景。你之前在大公司、年轻客群或者某某行业做得不错，但咱们这边主要是服务中老年顾客，阿姨们挺挑的。我有点担心你会不会不习惯，或者觉得是不是有点屈才了，你怎么看？"
    admin_store.save_interview_turn_events(
        token,
        "attempt-1",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": rewritten_question,
                "question_id": "q1",
                "question_index": 1,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": "我是感觉有一点点屈才了。",
                "question_id": "q1",
                "question_index": 1,
            },
        ],
    )
    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": original_question},
                    {"role": "candidate", "content": "我是感觉有一点点屈才了。"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-1",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_TURN_EVENTS
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    interviewer_turns = [item["content"] for item in detail["turns"] if item["role"] == "interviewer"]
    assert interviewer_turns.count(rewritten_question) == 1
    assert original_question not in interviewer_turns


def test_finalize_canonical_artifacts_events_only_score_inputs_stable(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1

    candidate_answer = "我先按团队流程走，再做小步优化。"
    admin_store.save_interview_turn_events(
        token,
        "attempt-1",
        [
            {
                "seq_no": 1,
                "role": "interviewer",
                "text": "第一题，请你介绍一个项目",
                "question_id": "q1",
                "question_index": 1,
            },
            {
                "seq_no": 2,
                "role": "candidate",
                "text": candidate_answer,
                "question_id": "q1",
                "question_index": 1,
            },
        ],
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert finalized["canonical_source"] == admin_store.CANONICAL_SOURCE_TURN_EVENTS
    assert len(finalized["score_inputs"]) == 1
    assert finalized["score_inputs"][0]["aggregated_answer"] == candidate_answer
    assert finalized["unanswered_zero_items"] == []


def test_finalize_canonical_artifacts_events_only_does_not_fallback_to_checkpoint(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
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
    assert admin_store.start_interview_attempt(token, "attempt-1", owner_id="owner-1") == 1

    checkpoint_payload = {
        "version": 1,
        "state": "WAIT_ANSWER",
        "current_question_index": 0,
        "total_candidate_turns": 1,
        "latest_follow_up": "",
        "latest_clarify": "",
        "questions": [
            {
                "question_id": "q1",
                "status": "in_progress",
                "question_epoch": 0,
                "follow_up_count": 0,
                "clarify_count": 0,
                "coverage_score": 0.0,
                "turns": [
                    {"role": "interviewer", "content": "第一题，请你介绍一个项目"},
                    {"role": "candidate", "content": "checkpoint 里的候选人回答"},
                ],
            }
        ],
    }
    assert (
        admin_store.save_interview_checkpoint_journal(
            token,
            "attempt-1",
            1,
            checkpoint_payload,
            source="flush",
        )
        is True
    )
    interviewer_mp3_like = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 128
    admin_store.persist_interview_audio(
        token=token,
        attempt_id="attempt-1",
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 800,
        interviewer_encoded_bytes=interviewer_mp3_like,
    )
    admin_store.close_interview_attempt(
        "attempt-1",
        status="completed",
        close_source="normal_end",
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is False
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    assert detail["canonical_status"] == admin_store.INTERVIEW_CANONICAL_STATUS_FAILED
    assert "canonical_turns_empty" in detail["consistency_flags"]


def _prepare_events_only_interview_for_stt(monkeypatch, tmp_path: Path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
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
    attempt_id = "attempt-1"
    assert admin_store.start_interview_attempt(token, attempt_id, owner_id="owner-1") == 1

    turn_events = [
        {
            "seq_no": 1,
            "role": "interviewer",
            "text": "请介绍一个项目",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": "",
            "question_index": 1,
            "question_epoch": 0,
            "commit_state": "live",
        },
        {
            "seq_no": 2,
            "role": "candidate",
            "text": "原答案一",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": "",
            "question_index": 1,
            "question_epoch": 0,
            "commit_state": "live",
        },
        {
            "seq_no": 3,
            "role": "candidate",
            "text": "原答案二",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": "",
            "question_index": 1,
            "question_epoch": 0,
            "commit_state": "live",
        },
    ]
    assert admin_store.save_interview_turn_events(token, attempt_id, turn_events) == 3
    admin_store.persist_interview_audio(
        token=token,
        attempt_id=attempt_id,
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 1200,
        interviewer_encoded_bytes=(b"\x01\x00\x02\x00") * 1200,
    )
    admin_store.close_interview_attempt(
        attempt_id,
        status="completed",
        close_source="normal_end",
    )
    return token


def test_finalize_canonical_artifacts_applies_stt_to_turns_and_score_inputs(monkeypatch, tmp_path):
    token = _prepare_events_only_interview_for_stt(monkeypatch, tmp_path)
    monkeypatch.setenv("STT_APP_ID", "stt-app")
    monkeypatch.setenv("STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("STT_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("STT_AUDIO_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "secret")

    called = {"count": 0}

    def _fake_transcribe(self, *, audio_url, audio_format, **_kwargs):
        called["count"] += 1
        assert audio_url.startswith("https://api.example.com/api/public/interviews/")
        assert "audio/candidate" in audio_url
        assert audio_format in {"wav", "mp3", "raw", "ogg"}
        return AucSTTResult(
            task_id="task-1",
            text="新答案一新答案二",
            utterances=["新答案一", "新答案二"],
            raw_payload={},
        )

    monkeypatch.setattr(
        admin_store.AucSTTClient,
        "transcribe_audio_url",
        _fake_transcribe,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert called["count"] == 1
    assert "stt_applied" in finalized["consistency_flags"]
    assert "stt_partial_fallback" not in finalized["consistency_flags"]
    assert "stt_full_fallback" not in finalized["consistency_flags"]
    assert finalized["score_inputs"][0]["aggregated_answer"] == "新答案一\n新答案二"

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    candidate_turns = [item["content"] for item in detail["turns"] if item["role"] == "candidate"]
    assert candidate_turns == ["新答案一", "新答案二"]


def test_finalize_canonical_artifacts_stt_failure_falls_back_to_asr(monkeypatch, tmp_path):
    token = _prepare_events_only_interview_for_stt(monkeypatch, tmp_path)
    monkeypatch.setenv("STT_APP_ID", "stt-app")
    monkeypatch.setenv("STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("STT_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("STT_AUDIO_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "secret")

    def _fake_transcribe_fail(self, **_kwargs):
        raise RuntimeError("mock_stt_failed")

    monkeypatch.setattr(
        admin_store.AucSTTClient,
        "transcribe_audio_url",
        _fake_transcribe_fail,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert "stt_full_fallback" in finalized["consistency_flags"]
    assert "stt_failed:RuntimeError" in finalized["consistency_flags"]
    assert finalized["score_inputs"][0]["aggregated_answer"] == "原答案一\n原答案二"


def test_finalize_canonical_artifacts_stt_partial_fallback(monkeypatch, tmp_path):
    token = _prepare_events_only_interview_for_stt(monkeypatch, tmp_path)
    monkeypatch.setenv("STT_APP_ID", "stt-app")
    monkeypatch.setenv("STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("STT_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("STT_AUDIO_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "secret")

    def _fake_transcribe_partial(self, **_kwargs):
        return AucSTTResult(
            task_id="task-1",
            text="只识别到一段",
            utterances=["只识别到一段"],
            raw_payload={},
        )

    monkeypatch.setattr(
        admin_store.AucSTTClient,
        "transcribe_audio_url",
        _fake_transcribe_partial,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert "stt_applied" in finalized["consistency_flags"]
    assert "stt_partial_fallback" in finalized["consistency_flags"]
    assert finalized["score_inputs"][0]["aggregated_answer"] == "只识别到一段\n原答案二"


def _prepare_events_only_interview_for_per_question_stt(monkeypatch, tmp_path: Path):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目一", "背景 职责 结果"), ("请介绍项目二", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    selected_questions = detail["selected_questions"]
    assert len(selected_questions) == 2
    q1 = f"q{int(selected_questions[0]['question_id'])}"
    q2 = f"q{int(selected_questions[1]['question_id'])}"

    assert admin_store.mark_interview_in_progress(token) is True
    attempt_id = "attempt-1"
    assert admin_store.start_interview_attempt(token, attempt_id, owner_id="owner-1") == 1

    turn_events = [
        {
            "seq_no": 1,
            "role": "interviewer",
            "text": "请介绍项目一",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": q1,
            "question_index": 1,
            "question_epoch": 0,
            "commit_state": "live",
        },
        {
            "seq_no": 2,
            "role": "candidate",
            "text": "题一原答案",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": q1,
            "question_index": 1,
            "question_epoch": 0,
            "commit_state": "live",
        },
        {
            "seq_no": 3,
            "role": "interviewer",
            "text": "请介绍项目二",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": q2,
            "question_index": 2,
            "question_epoch": 0,
            "commit_state": "live",
        },
        {
            "seq_no": 4,
            "role": "candidate",
            "text": "题二原答案",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": q2,
            "question_index": 2,
            "question_epoch": 0,
            "commit_state": "live",
        },
    ]
    assert admin_store.save_interview_turn_events(token, attempt_id, turn_events) == 4
    admin_store.persist_interview_audio(
        token=token,
        attempt_id=attempt_id,
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 1600,
        interviewer_encoded_bytes=(b"\x01\x00\x02\x00") * 1600,
    )
    admin_store.persist_interview_question_audio_segments(
        token,
        attempt_id=attempt_id,
        question_candidate_pcm_segments=[
            {
                "question_id": q1,
                "question_epoch": 0,
                "pcm_bytes": (b"\x01\x00\x02\x00") * 800,
            },
            {
                "question_id": q2,
                "question_epoch": 0,
                "pcm_bytes": (b"\x01\x00\x02\x00") * 800,
            },
        ],
    )
    admin_store.close_interview_attempt(
        attempt_id,
        status="completed",
        close_source="normal_end",
    )
    return token, q1, q2


def test_finalize_canonical_artifacts_applies_per_question_stt(monkeypatch, tmp_path):
    token, q1, q2 = _prepare_events_only_interview_for_per_question_stt(
        monkeypatch, tmp_path
    )
    monkeypatch.setenv("STT_APP_ID", "stt-app")
    monkeypatch.setenv("STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("STT_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("STT_AUDIO_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "secret")
    monkeypatch.setenv("STT_PER_QUESTION_ENABLED", "true")

    called = []

    def _fake_transcribe(self, *, audio_url, audio_format, **_kwargs):
        parsed = urlparse(audio_url)
        query = parse_qs(parsed.query)
        question_id = str(query.get("question_id", [""])[0] or "")
        question_epoch = int(query.get("question_epoch", ["0"])[0] or 0)
        called.append((question_id, question_epoch, audio_format))
        assert audio_format in {"wav", "mp3", "raw", "ogg"}
        if question_id == q1:
            return AucSTTResult(
                task_id="task-q1",
                text="题一-STT",
                utterances=["题一-STT"],
                raw_payload={},
            )
        if question_id == q2:
            return AucSTTResult(
                task_id="task-q2",
                text="题二-STT",
                utterances=["题二-STT"],
                raw_payload={},
            )
        raise RuntimeError("unexpected_question")

    monkeypatch.setattr(
        admin_store.AucSTTClient,
        "transcribe_audio_url",
        _fake_transcribe,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert "stt_applied" in finalized["consistency_flags"]
    assert "stt_partial_fallback" not in finalized["consistency_flags"]
    assert sorted((item[0], item[1]) for item in called) == sorted([(q1, 0), (q2, 0)])
    assert [item["aggregated_answer"] for item in finalized["score_inputs"]] == [
        "题一-STT",
        "题二-STT",
    ]

    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    candidate_turns = [item["content"] for item in detail["turns"] if item["role"] == "candidate"]
    assert candidate_turns == ["题一-STT", "题二-STT"]


def test_finalize_canonical_artifacts_per_question_stt_partial_fallback(
    monkeypatch, tmp_path
):
    token, q1, q2 = _prepare_events_only_interview_for_per_question_stt(
        monkeypatch, tmp_path
    )
    monkeypatch.setenv("STT_APP_ID", "stt-app")
    monkeypatch.setenv("STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("STT_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("STT_AUDIO_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "secret")
    monkeypatch.setenv("STT_PER_QUESTION_ENABLED", "true")

    def _fake_transcribe(self, *, audio_url, **_kwargs):
        question_id = str(parse_qs(urlparse(audio_url).query).get("question_id", [""])[0])
        if question_id == q1:
            return AucSTTResult(
                task_id="task-q1",
                text="题一-STT",
                utterances=["题一-STT"],
                raw_payload={},
            )
        if question_id == q2:
            raise RuntimeError("mock_q2_failed")
        raise RuntimeError("unexpected_question")

    monkeypatch.setattr(
        admin_store.AucSTTClient,
        "transcribe_audio_url",
        _fake_transcribe,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    assert "stt_applied" in finalized["consistency_flags"]
    assert "stt_partial_fallback" in finalized["consistency_flags"]
    assert "stt_failed:RuntimeError" in finalized["consistency_flags"]
    assert [item["aggregated_answer"] for item in finalized["score_inputs"]] == [
        "题一-STT",
        "题二原答案",
    ]


def test_finalize_canonical_artifacts_per_question_stt_respects_question_epoch(
    monkeypatch, tmp_path
):
    _setup_tmp_store(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    monkeypatch.setenv("CANONICAL_EVENTS_ONLY_MIN_CREATED_AT", "1970-01-01T00:00:00+00:00")
    monkeypatch.setenv("AUDIO_COMPRESS_ENABLED", "0")
    admin_store.ensure_default_admin()

    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("请介绍项目", "背景 职责 结果")],
    )
    interview = admin_store.create_interview(
        candidate_name="测试用户",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=_followups_for_job(job["job_uid"]),
    )
    token = interview["token"]
    detail = admin_store.get_interview_detail(token)
    assert detail is not None
    q1 = f"q{int(detail['selected_questions'][0]['question_id'])}"

    assert admin_store.mark_interview_in_progress(token) is True
    attempt_id = "attempt-1"
    assert admin_store.start_interview_attempt(token, attempt_id, owner_id="owner-1") == 1

    turn_events = [
        {
            "seq_no": 1,
            "role": "candidate",
            "text": "epoch0-原答案",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": q1,
            "question_index": 1,
            "question_epoch": 0,
            "commit_state": "live",
        },
        {
            "seq_no": 2,
            "role": "candidate",
            "text": "epoch1-原答案",
            "event_ts": datetime.now(timezone.utc).isoformat(),
            "event_kind": "transcript",
            "question_id": q1,
            "question_index": 1,
            "question_epoch": 1,
            "commit_state": "live",
        },
    ]
    assert admin_store.save_interview_turn_events(token, attempt_id, turn_events) == 2
    admin_store.persist_interview_audio(
        token=token,
        attempt_id=attempt_id,
        candidate_pcm_bytes=(b"\x01\x00\x02\x00") * 1600,
        interviewer_encoded_bytes=(b"\x01\x00\x02\x00") * 1600,
    )
    admin_store.persist_interview_question_audio_segments(
        token,
        attempt_id=attempt_id,
        question_candidate_pcm_segments=[
            {
                "question_id": q1,
                "question_epoch": 0,
                "pcm_bytes": (b"\x01\x00\x02\x00") * 800,
            },
            {
                "question_id": q1,
                "question_epoch": 1,
                "pcm_bytes": (b"\x01\x00\x02\x00") * 800,
            },
        ],
    )
    admin_store.close_interview_attempt(
        attempt_id,
        status="completed",
        close_source="normal_end",
    )

    monkeypatch.setenv("STT_APP_ID", "stt-app")
    monkeypatch.setenv("STT_ACCESS_TOKEN", "stt-token")
    monkeypatch.setenv("STT_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("STT_AUDIO_PUBLIC_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("STT_AUDIO_SIGNING_SECRET", "secret")
    monkeypatch.setenv("STT_PER_QUESTION_ENABLED", "true")

    def _fake_transcribe(self, *, audio_url, **_kwargs):
        question_epoch = int(
            parse_qs(urlparse(audio_url).query).get("question_epoch", ["0"])[0] or 0
        )
        if question_epoch == 0:
            return AucSTTResult(
                task_id="task-e0",
                text="epoch0-STT",
                utterances=["epoch0-STT"],
                raw_payload={},
            )
        if question_epoch == 1:
            return AucSTTResult(
                task_id="task-e1",
                text="epoch1-STT",
                utterances=["epoch1-STT"],
                raw_payload={},
            )
        raise RuntimeError("unexpected_epoch")

    monkeypatch.setattr(
        admin_store.AucSTTClient,
        "transcribe_audio_url",
        _fake_transcribe,
    )

    finalized = admin_store.finalize_canonical_artifacts(token)
    assert finalized["ok"] is True
    detail_after = admin_store.get_interview_detail(token)
    assert detail_after is not None
    candidate_turns = [item["content"] for item in detail_after["turns"] if item["role"] == "candidate"]
    assert candidate_turns == ["epoch0-STT", "epoch1-STT"]
