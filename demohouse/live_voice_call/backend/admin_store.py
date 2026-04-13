import difflib
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from arkitect.telemetry.logger import INFO
from redis import Redis
from redis.exceptions import RedisError
from interview_cache import CACHE_MISS, InterviewTokenCache

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
STORAGE_DIR = DATA_DIR / "storage"
AUDIO_DIR = STORAGE_DIR / "audio"
INTERVIEW_LOG_DIR = STORAGE_DIR / "interview_logs"

ADMIN_SESSION_COOKIE = "admin_session"

INTERVIEW_STATUS_PENDING = "pending"
INTERVIEW_STATUS_IN_PROGRESS = "in_progress"
INTERVIEW_STATUS_COMPLETED = "completed"
INTERVIEW_STATUS_FAILED = "failed"
INTERVIEW_STATUS_DELETED = "deleted"
INTERVIEW_CANONICAL_STATUS_READY = "ready"
INTERVIEW_CANONICAL_STATUS_BUILDING = "building"
INTERVIEW_CANONICAL_STATUS_FAILED = "failed"
CANONICAL_SOURCE_CHECKPOINT = "checkpoint"
CANONICAL_SOURCE_TURN_EVENTS_FALLBACK = "turn_events_fallback"
SCORECARD_STATUS_PENDING = "pending"
SCORECARD_STATUS_COMPLETED = "completed"
SCORECARD_STATUS_FAILED = "failed"
INTERVIEW_COMPLETED_REASON_NORMAL_END = "normal_end"
INTERVIEW_COMPLETED_REASON_HANGUP = "hangup"
INTERVIEW_COMPLETED_REASON_DISCONNECT = "disconnect"
INTERVIEW_COMPLETED_REASON_ERROR = "error"
INTERVIEW_COMPLETED_REASONS = {
    INTERVIEW_COMPLETED_REASON_NORMAL_END,
    INTERVIEW_COMPLETED_REASON_HANGUP,
    INTERVIEW_COMPLETED_REASON_DISCONNECT,
    INTERVIEW_COMPLETED_REASON_ERROR,
}
BEST_EFFORT_COMPLETED_REASONS = {
    INTERVIEW_COMPLETED_REASON_HANGUP,
    INTERVIEW_COMPLETED_REASON_DISCONNECT,
    INTERVIEW_COMPLETED_REASON_ERROR,
}
MAX_INTERRUPTION_COUNT = 3
CHECKIN_ORDER = ("speaker", "mic", "camera", "screen")
DEFAULT_REQUIRED_CHECKINS = ("speaker", "mic")
FOLLOWUP_MIN = 0
FOLLOWUP_MAX = 3
DEFAULT_QUESTION_MAX_FOLLOWUPS = 0
DEFAULT_QUESTION_MAX_CLARIFIES = 1
INTERVIEW_TOKEN_TTL_HOURS = 24
INTERVIEW_EXPIRY_KEY_PREFIX = (os.getenv("INTERVIEW_EXPIRY_KEY_PREFIX") or "interview:expiry").strip()
INTERVIEW_EXPIRY_SWEEP_SECONDS = max(
    1, int(os.getenv("INTERVIEW_EXPIRY_SWEEP_SECONDS", "10"))
)
INTERVIEW_EXPIRY_SWEEP_BATCH_SIZE = max(
    1, int(os.getenv("INTERVIEW_EXPIRY_SWEEP_BATCH_SIZE", "200"))
)

INTERVIEW_CACHE = InterviewTokenCache()


class InterviewExpiryIndex:
    def __init__(self) -> None:
        self.redis_url = (os.getenv("REDIS_URL") or "").strip()
        self.key_prefix = INTERVIEW_EXPIRY_KEY_PREFIX
        self._client: Optional[Redis] = None

    def _key(self) -> str:
        return f"{self.key_prefix}:zset"

    def _get_client(self) -> Optional[Redis]:
        if not self.redis_url:
            return None
        if self._client is None:
            self._client = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        return self._client

    def ensure_ready(self) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(client.ping())
        except RedisError:
            return False

    def schedule(self, token: str, expires_at: Optional[str]) -> bool:
        client = self._get_client()
        if client is None:
            return False
        expires_at_dt = parse_iso_or_none(expires_at)
        if not token or not expires_at_dt:
            return False
        try:
            client.zadd(self._key(), {token: int(expires_at_dt.timestamp() * 1000)})
            return True
        except RedisError:
            return False

    def remove(self, token: str) -> bool:
        client = self._get_client()
        if client is None:
            return False
        if not token:
            return False
        try:
            client.zrem(self._key(), token)
            return True
        except RedisError:
            return False

    def due_tokens(self, now_ms: Optional[int] = None, *, limit: int = 200) -> Optional[List[str]]:
        client = self._get_client()
        if client is None:
            return None
        current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        try:
            entries = client.zrangebyscore(self._key(), "-inf", current_ms, start=0, num=max(1, int(limit)))
            return [str(item) for item in entries if item]
        except RedisError:
            return None


INTERVIEW_EXPIRY_INDEX = InterviewExpiryIndex()


@dataclass
class InterviewSessionData:
    token: str
    candidate_name: str
    job_uid: str
    job_name: str
    status: str
    questions: List[Dict[str, object]]


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS admins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
  token TEXT PRIMARY KEY,
  admin_id INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(admin_id) REFERENCES admins(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
  job_uid TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  duties TEXT NOT NULL,
  requirements TEXT NOT NULL,
  notes TEXT,
  csv_filename TEXT,
  question_bank_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_uid TEXT NOT NULL,
  bank_version INTEGER NOT NULL DEFAULT 1,
  scenario TEXT NOT NULL DEFAULT '',
  question TEXT NOT NULL,
  reference_answer TEXT NOT NULL,
  ability_dimension TEXT NOT NULL DEFAULT '',
  scoring_boundary TEXT NOT NULL DEFAULT '',
  best_standard TEXT NOT NULL DEFAULT '',
  medium_standard TEXT NOT NULL DEFAULT '',
  worst_standard TEXT NOT NULL DEFAULT '',
  output_format TEXT NOT NULL DEFAULT '',
  score_format TEXT NOT NULL DEFAULT '',
  comment_requirement TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL,
  FOREIGN KEY(job_uid) REFERENCES jobs(job_uid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interviews (
  token TEXT PRIMARY KEY,
  candidate_name TEXT NOT NULL,
  job_uid TEXT NOT NULL,
  question_bank_version INTEGER NOT NULL DEFAULT 1,
  duration_minutes INTEGER NOT NULL DEFAULT 0,
  question_count INTEGER NOT NULL,
  required_checkins TEXT NOT NULL DEFAULT 'speaker,mic',
  selected_question_ids TEXT NOT NULL,
  question_followup_limits TEXT NOT NULL DEFAULT '',
  question_clarify_limits TEXT NOT NULL DEFAULT '',
  notes TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  completed_at TEXT,
  completed_reason TEXT,
  interruption_count INTEGER NOT NULL DEFAULT 0,
  reconnect_deadline_at TEXT,
  canonical_status TEXT NOT NULL DEFAULT 'ready',
  canonical_version INTEGER NOT NULL DEFAULT 0,
  canonical_source TEXT NOT NULL DEFAULT 'turn_events_fallback',
  discarded_turn_count INTEGER NOT NULL DEFAULT 0,
  echo_risk_flags TEXT NOT NULL DEFAULT '',
  consistency_flags TEXT NOT NULL DEFAULT '',
  candidate_audio_path TEXT,
  interviewer_audio_path TEXT,
  FOREIGN KEY(job_uid) REFERENCES jobs(job_uid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interview_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL UNIQUE,
  interview_token TEXT NOT NULL,
  client_id TEXT NOT NULL DEFAULT '',
  owner_id TEXT NOT NULL DEFAULT '',
  attempt_seq INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'in_progress',
  started_at TEXT NOT NULL,
  ended_at TEXT,
  close_source TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interview_turn_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL,
  interview_token TEXT NOT NULL,
  seq_no INTEGER NOT NULL,
  event_ts TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  event_kind TEXT NOT NULL DEFAULT 'transcript',
  question_id TEXT NOT NULL DEFAULT '',
  question_index INTEGER NOT NULL DEFAULT 0,
  question_epoch INTEGER NOT NULL DEFAULT 0,
  commit_state TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE,
  FOREIGN KEY(attempt_id) REFERENCES interview_attempts(attempt_id) ON DELETE CASCADE,
  UNIQUE(attempt_id, seq_no)
);

CREATE TABLE IF NOT EXISTS interview_audio_segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL,
  interview_token TEXT NOT NULL,
  track TEXT NOT NULL,
  segment_seq INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE,
  FOREIGN KEY(attempt_id) REFERENCES interview_attempts(attempt_id) ON DELETE CASCADE,
  UNIQUE(attempt_id, track, segment_seq)
);

CREATE TABLE IF NOT EXISTS interview_checkpoint_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  interview_token TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  checkpoint_seq INTEGER NOT NULL,
  checkpoint_ts TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE,
  FOREIGN KEY(attempt_id) REFERENCES interview_attempts(attempt_id) ON DELETE CASCADE,
  UNIQUE(attempt_id, checkpoint_seq)
);

CREATE TABLE IF NOT EXISTS interview_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  interview_token TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interview_scorecards (
  interview_token TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending',
  overall_score REAL,
  total_score REAL,
  total_max_score REAL,
  error_message TEXT,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interview_question_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  interview_token TEXT NOT NULL,
  question_id TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  question TEXT NOT NULL,
  ability_dimension TEXT NOT NULL DEFAULT '',
  output_format TEXT NOT NULL DEFAULT '',
  score_format TEXT NOT NULL DEFAULT '',
  comment_requirement TEXT NOT NULL DEFAULT '',
  aggregated_answer TEXT NOT NULL DEFAULT '',
  max_score REAL NOT NULL DEFAULT 0,
  score_error TEXT NOT NULL DEFAULT '',
  numeric_score REAL NOT NULL DEFAULT 0,
  comment TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(interview_token) REFERENCES interviews(token) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interview_attempts_token_seq
ON interview_attempts(interview_token, attempt_seq);

CREATE INDEX IF NOT EXISTS idx_interview_turn_events_token_ts
ON interview_turn_events(interview_token, event_ts, id);

CREATE INDEX IF NOT EXISTS idx_interview_audio_segments_token_track_seq
ON interview_audio_segments(interview_token, track, id);

CREATE INDEX IF NOT EXISTS idx_interview_checkpoint_journal_token_seq
ON interview_checkpoint_journal(interview_token, id);

"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_interview_expires_at(created_at_iso: str) -> str:
    created_at = parse_iso_or_none(created_at_iso) or datetime.now(timezone.utc)
    return (created_at + timedelta(hours=INTERVIEW_TOKEN_TTL_HOURS)).isoformat()


def _is_interview_expired(expires_at: Optional[str], now: datetime) -> bool:
    parsed = parse_iso_or_none(expires_at)
    if not parsed:
        return False
    return now >= parsed


def _invalidate_interview_cache(token: str) -> None:
    if token:
        INTERVIEW_CACHE.invalidate(token)


def parse_iso_or_none(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    INTERVIEW_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        _apply_schema_migrations(conn)
        conn.commit()


def _apply_schema_migrations(conn: sqlite3.Connection) -> None:
    job_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "question_bank_version" not in job_columns:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN question_bank_version INTEGER NOT NULL DEFAULT 1"
        )

    interview_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(interviews)").fetchall()
    }
    if "interruption_count" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN interruption_count INTEGER NOT NULL DEFAULT 0"
        )
    if "reconnect_deadline_at" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN reconnect_deadline_at TEXT"
        )
    if "required_checkins" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN required_checkins TEXT NOT NULL DEFAULT 'speaker,mic'"
        )
        conn.execute(
            "UPDATE interviews SET required_checkins = 'speaker,mic' WHERE required_checkins IS NULL"
        )
    if "question_followup_limits" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN question_followup_limits TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "UPDATE interviews SET question_followup_limits = '' WHERE question_followup_limits IS NULL"
        )
    if "question_clarify_limits" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN question_clarify_limits TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "UPDATE interviews SET question_clarify_limits = '' WHERE question_clarify_limits IS NULL"
        )
    if "expires_at" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN expires_at TEXT"
        )
    if "completed_reason" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN completed_reason TEXT"
        )
    if "question_bank_version" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN question_bank_version INTEGER NOT NULL DEFAULT 1"
        )
    if "canonical_status" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN canonical_status TEXT NOT NULL DEFAULT 'ready'"
        )
    if "canonical_version" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN canonical_version INTEGER NOT NULL DEFAULT 0"
        )
    if "consistency_flags" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN consistency_flags TEXT NOT NULL DEFAULT ''"
        )
    if "canonical_source" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN canonical_source TEXT NOT NULL DEFAULT 'turn_events_fallback'"
        )
    if "discarded_turn_count" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN discarded_turn_count INTEGER NOT NULL DEFAULT 0"
        )
    if "echo_risk_flags" not in interview_columns:
        conn.execute(
            "ALTER TABLE interviews ADD COLUMN echo_risk_flags TEXT NOT NULL DEFAULT ''"
        )
    expires_rows = conn.execute(
        "SELECT token, created_at FROM interviews WHERE expires_at IS NULL OR TRIM(expires_at) = ''"
    ).fetchall()
    for row in expires_rows:
        conn.execute(
            "UPDATE interviews SET expires_at = ? WHERE token = ?",
            (_compute_interview_expires_at(row["created_at"]), row["token"]),
        )
    conn.execute(
        "UPDATE interviews SET canonical_status = 'ready' "
        "WHERE canonical_status IS NULL OR TRIM(canonical_status) = ''"
    )
    conn.execute(
        "UPDATE interviews SET consistency_flags = '' "
        "WHERE consistency_flags IS NULL"
    )
    conn.execute(
        "UPDATE interviews SET canonical_source = 'turn_events_fallback' "
        "WHERE canonical_source IS NULL OR TRIM(canonical_source) = ''"
    )
    conn.execute(
        "UPDATE interviews SET discarded_turn_count = 0 "
        "WHERE discarded_turn_count IS NULL"
    )
    conn.execute(
        "UPDATE interviews SET echo_risk_flags = '' "
        "WHERE echo_risk_flags IS NULL"
    )

    turn_event_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(interview_turn_events)").fetchall()
    }
    if "event_kind" not in turn_event_columns:
        conn.execute(
            "ALTER TABLE interview_turn_events ADD COLUMN event_kind TEXT NOT NULL DEFAULT 'transcript'"
        )
    if "question_epoch" not in turn_event_columns:
        conn.execute(
            "ALTER TABLE interview_turn_events ADD COLUMN question_epoch INTEGER NOT NULL DEFAULT 0"
        )
    if "commit_state" not in turn_event_columns:
        conn.execute(
            "ALTER TABLE interview_turn_events ADD COLUMN commit_state TEXT NOT NULL DEFAULT ''"
        )

    question_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(job_questions)").fetchall()
    }
    if "ability_dimension" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN ability_dimension TEXT NOT NULL DEFAULT ''"
        )
    if "scenario" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN scenario TEXT NOT NULL DEFAULT ''"
        )
    if "scoring_boundary" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN scoring_boundary TEXT NOT NULL DEFAULT ''"
        )
    if "best_standard" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN best_standard TEXT NOT NULL DEFAULT ''"
        )
    if "medium_standard" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN medium_standard TEXT NOT NULL DEFAULT ''"
        )
    if "worst_standard" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN worst_standard TEXT NOT NULL DEFAULT ''"
        )
    if "output_format" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN output_format TEXT NOT NULL DEFAULT ''"
        )
    if "score_format" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN score_format TEXT NOT NULL DEFAULT ''"
        )
    if "comment_requirement" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN comment_requirement TEXT NOT NULL DEFAULT ''"
        )
    if "bank_version" not in question_columns:
        conn.execute(
            "ALTER TABLE job_questions ADD COLUMN bank_version INTEGER NOT NULL DEFAULT 1"
        )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_job_questions_job_uid_bank_version_sort_order
        ON job_questions(job_uid, bank_version, sort_order)
        """
    )

    question_score_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(interview_question_scores)").fetchall()
    }
    if "score_format" not in question_score_columns:
        conn.execute(
            "ALTER TABLE interview_question_scores ADD COLUMN score_format TEXT NOT NULL DEFAULT ''"
        )
    if "comment_requirement" not in question_score_columns:
        conn.execute(
            "ALTER TABLE interview_question_scores ADD COLUMN comment_requirement TEXT NOT NULL DEFAULT ''"
        )
    if "max_score" not in question_score_columns:
        conn.execute(
            "ALTER TABLE interview_question_scores ADD COLUMN max_score REAL NOT NULL DEFAULT 0"
        )
    if "score_error" not in question_score_columns:
        conn.execute(
            "ALTER TABLE interview_question_scores ADD COLUMN score_error TEXT NOT NULL DEFAULT ''"
        )

    scorecard_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(interview_scorecards)").fetchall()
    }
    if "total_score" not in scorecard_columns:
        conn.execute(
            "ALTER TABLE interview_scorecards ADD COLUMN total_score REAL"
        )
    if "total_max_score" not in scorecard_columns:
        conn.execute(
            "ALTER TABLE interview_scorecards ADD COLUMN total_max_score REAL"
        )


def normalize_required_checkins(required_checkins: Optional[Sequence[str]]) -> List[str]:
    if required_checkins is None:
        return list(DEFAULT_REQUIRED_CHECKINS)
    if isinstance(required_checkins, str):
        raise ValueError("invalid_required_checkins")

    seen: Set[str] = set()
    for item in required_checkins:
        if not isinstance(item, str):
            raise ValueError("invalid_required_checkins")
        normalized = item.strip()
        if not normalized:
            continue
        if normalized not in CHECKIN_ORDER:
            raise ValueError("invalid_required_checkins")
        seen.add(normalized)
    return [step for step in CHECKIN_ORDER if step in seen]


def serialize_required_checkins(required_checkins: Sequence[str]) -> str:
    return ",".join(required_checkins)


def parse_required_checkins(raw: Optional[str]) -> List[str]:
    if raw is None:
        return list(DEFAULT_REQUIRED_CHECKINS)
    values = {item.strip() for item in raw.split(",") if item.strip()}
    return [step for step in CHECKIN_ORDER if step in values]


def _normalize_question_followups(
    question_followups: Optional[Sequence[Dict[str, Any]]],
    question_ids: Sequence[int],
) -> Tuple[Dict[int, int], Dict[int, int]]:
    expected_ids = list(question_ids)
    expected_set = set(expected_ids)
    if question_followups is None:
        return (
            {qid: DEFAULT_QUESTION_MAX_FOLLOWUPS for qid in expected_ids},
            {qid: DEFAULT_QUESTION_MAX_CLARIFIES for qid in expected_ids},
        )
    if isinstance(question_followups, str):
        raise ValueError("invalid_question_followups")

    parsed_followups: Dict[int, int] = {}
    parsed_clarifies: Dict[int, int] = {}
    for item in question_followups:
        if not isinstance(item, dict):
            raise ValueError("invalid_question_followups")
        raw_qid = item.get("question_id")
        raw_followup_limit = item.get("max_followups")
        raw_clarify_limit = item.get("max_clarifies")
        if not isinstance(raw_qid, int) or raw_qid <= 0:
            raise ValueError("invalid_question_followups")
        if not isinstance(raw_followup_limit, int):
            raise ValueError("invalid_question_followups")
        if raw_followup_limit < FOLLOWUP_MIN or raw_followup_limit > FOLLOWUP_MAX:
            raise ValueError("invalid_question_followups")
        if not isinstance(raw_clarify_limit, int):
            raise ValueError("invalid_question_followups")
        if raw_clarify_limit < FOLLOWUP_MIN or raw_clarify_limit > FOLLOWUP_MAX:
            raise ValueError("invalid_question_followups")
        if raw_qid in parsed_followups:
            raise ValueError("invalid_question_followups")
        parsed_followups[raw_qid] = raw_followup_limit
        parsed_clarifies[raw_qid] = raw_clarify_limit

    if set(parsed_followups.keys()) != expected_set:
        raise ValueError("invalid_question_followups")
    return parsed_followups, parsed_clarifies


def _serialize_question_followups(question_followups: Dict[int, int]) -> str:
    payload = {str(qid): max_followups for qid, max_followups in sorted(question_followups.items())}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_question_limits(
    raw: Optional[str],
    question_ids: Sequence[int],
    *,
    default_limit: int,
) -> Dict[int, int]:
    defaults = {qid: default_limit for qid in question_ids}
    if not raw:
        return defaults
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return defaults
    if not isinstance(payload, dict):
        return defaults

    parsed: Dict[int, int] = {}
    for key, value in payload.items():
        try:
            qid = int(str(key).strip())
            limit = int(value)
        except (TypeError, ValueError):
            continue
        if qid not in defaults:
            continue
        if limit < FOLLOWUP_MIN or limit > FOLLOWUP_MAX:
            continue
        parsed[qid] = limit
    return {qid: parsed.get(qid, default_limit) for qid in question_ids}


def _parse_question_followups(raw: Optional[str], question_ids: Sequence[int]) -> Dict[int, int]:
    return _parse_question_limits(
        raw,
        question_ids,
        default_limit=DEFAULT_QUESTION_MAX_FOLLOWUPS,
    )


def _parse_question_clarifies(raw: Optional[str], question_ids: Sequence[int]) -> Dict[int, int]:
    return _parse_question_limits(
        raw,
        question_ids,
        default_limit=DEFAULT_QUESTION_MAX_CLARIFIES,
    )


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA read_uncommitted = OFF")
    return conn


def _begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _generate_hash(password: str, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = os.urandom(16)
    rounds = 120_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${dk.hex()}"


def _verify_hash(password: str, hashed: str) -> bool:
    try:
        method, rounds_text, salt_hex, digest_hex = hashed.split("$", 3)
        if method != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except Exception:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return secrets.compare_digest(calc, expected)


def ensure_default_admin() -> None:
    ensure_storage()
    username = (os.getenv("ADMIN_USERNAME") or "admin").strip()
    password = os.getenv("ADMIN_PASSWORD") or "admin123456"
    now = utc_now_iso()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM admins WHERE username = ?",
            (username,),
        ).fetchone()
        if row:
            return
        conn.execute(
            "INSERT INTO admins (username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (username, _generate_hash(password), now, now),
        )
        conn.commit()


def verify_admin_credentials(username: str, password: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM admins WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return None
        if not _verify_hash(password, row["password_hash"]):
            return None
        return int(row["id"])


def create_admin_session(admin_id: int, ttl_hours: int = 12) -> str:
    token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token, admin_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, admin_id, expires_at, now.isoformat()),
        )
        conn.commit()
    return token


def revoke_admin_session(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
        conn.commit()


def get_admin_by_session(token: str) -> Optional[Dict[str, object]]:
    if not token:
        return None
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT s.token, s.expires_at, a.id AS admin_id, a.username
            FROM admin_sessions s
            JOIN admins a ON a.id = s.admin_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        expires_at = parse_iso_or_none(row["expires_at"])
        if not expires_at or expires_at <= now:
            conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        return {
            "admin_id": int(row["admin_id"]),
            "username": row["username"],
        }


def _generate_job_uid() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    return f"JOB-{date_part}-{secrets.token_hex(3).upper()}"


def _generate_interview_token() -> str:
    return f"INT-{secrets.token_urlsafe(10).replace('-', '').replace('_', '')[:14]}"


def _ensure_unique_job_uid(conn: sqlite3.Connection) -> str:
    for _ in range(20):
        uid = _generate_job_uid()
        row = conn.execute("SELECT job_uid FROM jobs WHERE job_uid = ?", (uid,)).fetchone()
        if not row:
            return uid
    raise RuntimeError("failed to generate unique job uid")


def _ensure_unique_interview_token(conn: sqlite3.Connection) -> str:
    for _ in range(20):
        token = _generate_interview_token()
        row = conn.execute("SELECT token FROM interviews WHERE token = ?", (token,)).fetchone()
        if not row:
            return token
    raise RuntimeError("failed to generate unique interview token")


def _normalize_job_questions(
    questions: Sequence[Union[Tuple[str, str], Dict[str, str]]],
) -> List[Dict[str, str]]:
    normalized_questions: List[Dict[str, str]] = []
    for item in questions:
        if isinstance(item, dict):
            question = (item.get("question") or "").strip()
            scoring_boundary = (item.get("scoring_boundary") or "").strip()
            best_standard = (item.get("best_standard") or "").strip()
            reference_answer = best_standard or scoring_boundary
            normalized_questions.append(
                {
                    "scenario": (item.get("scenario") or "").strip(),
                    "question": question,
                    "reference_answer": reference_answer,
                    "ability_dimension": (item.get("ability_dimension") or "").strip(),
                    "scoring_boundary": scoring_boundary,
                    "best_standard": best_standard,
                    "medium_standard": (item.get("medium_standard") or "").strip(),
                    "worst_standard": (item.get("worst_standard") or "").strip(),
                    "output_format": "",
                    "score_format": (item.get("score_format") or "").strip(),
                    "comment_requirement": (item.get("comment_requirement") or "").strip(),
                }
            )
            continue

        if isinstance(item, tuple) and len(item) >= 2:
            question = str(item[0]).strip()
            reference_answer = str(item[1]).strip()
            normalized_questions.append(
                {
                    "scenario": "",
                    "question": question,
                    "reference_answer": reference_answer,
                    "ability_dimension": "",
                    "scoring_boundary": "",
                    "best_standard": reference_answer,
                    "medium_standard": "",
                    "worst_standard": "",
                    "output_format": "",
                    "score_format": "",
                    "comment_requirement": "",
                }
            )
            continue

        raise ValueError("invalid_question_payload")

    return normalized_questions


def create_job(
    name: str,
    duties: str,
    requirements: str,
    notes: Optional[str],
    csv_filename: Optional[str],
    questions: Sequence[Union[Tuple[str, str], Dict[str, str]]],
) -> Dict[str, object]:
    now = utc_now_iso()
    initial_bank_version = 1
    normalized_questions = _normalize_job_questions(questions)

    with get_conn() as conn:
        _begin_immediate(conn)
        job_uid = _ensure_unique_job_uid(conn)
        conn.execute(
            """
            INSERT INTO jobs (
                job_uid, name, duties, requirements, notes, csv_filename,
                question_bank_version, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_uid,
                name,
                duties,
                requirements,
                notes,
                csv_filename,
                initial_bank_version,
                now,
                now,
            ),
        )
        for idx, question_payload in enumerate(normalized_questions):
            conn.execute(
                """
                INSERT INTO job_questions (
                    job_uid, bank_version, scenario, question, reference_answer, ability_dimension, scoring_boundary,
                    best_standard, medium_standard, worst_standard, output_format,
                    score_format, comment_requirement, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_uid,
                    initial_bank_version,
                    question_payload["scenario"],
                    question_payload["question"],
                    question_payload["reference_answer"],
                    question_payload["ability_dimension"],
                    question_payload["scoring_boundary"],
                    question_payload["best_standard"],
                    question_payload["medium_standard"],
                    question_payload["worst_standard"],
                    question_payload["output_format"],
                    question_payload["score_format"],
                    question_payload["comment_requirement"],
                    idx,
                ),
            )
        conn.commit()
    return {
        "job_uid": job_uid,
        "name": name,
        "question_count": len(normalized_questions),
        "question_bank_version": initial_bank_version,
        "created_at": now,
        "updated_at": now,
    }


def update_job(
    job_uid: str,
    name: str,
    duties: str,
    requirements: str,
    notes: Optional[str],
    expected_updated_at: str,
    csv_filename: Optional[str] = None,
    questions: Optional[Sequence[Union[Tuple[str, str], Dict[str, str]]]] = None,
) -> Dict[str, object]:
    if not expected_updated_at.strip():
        raise ValueError("invalid_expected_updated_at")
    now = utc_now_iso()

    normalized_questions: Optional[List[Dict[str, str]]] = None
    if questions is not None:
        normalized_questions = _normalize_job_questions(questions)

    with get_conn() as conn:
        _begin_immediate(conn)
        existing_job = conn.execute(
            "SELECT question_bank_version FROM jobs WHERE job_uid = ?",
            (job_uid,),
        ).fetchone()
        if not existing_job:
            raise ValueError("job_not_found")

        if normalized_questions is None:
            updated = conn.execute(
                """
                UPDATE jobs
                SET name = ?, duties = ?, requirements = ?, notes = ?, updated_at = ?
                WHERE job_uid = ? AND updated_at = ?
                """,
                (name, duties, requirements, notes, now, job_uid, expected_updated_at),
            )
            if updated.rowcount <= 0:
                raise ValueError("job_conflict")
        else:
            next_bank_version = int(existing_job["question_bank_version"] or 1) + 1
            updated = conn.execute(
                """
                UPDATE jobs
                SET name = ?, duties = ?, requirements = ?, notes = ?, csv_filename = ?,
                    question_bank_version = ?, updated_at = ?
                WHERE job_uid = ? AND updated_at = ?
                """,
                (
                    name,
                    duties,
                    requirements,
                    notes,
                    csv_filename,
                    next_bank_version,
                    now,
                    job_uid,
                    expected_updated_at,
                ),
            )
            if updated.rowcount <= 0:
                raise ValueError("job_conflict")

            for idx, question_payload in enumerate(normalized_questions):
                conn.execute(
                    """
                    INSERT INTO job_questions (
                        job_uid, bank_version, scenario, question, reference_answer,
                        ability_dimension, scoring_boundary, best_standard, medium_standard,
                        worst_standard, output_format, score_format, comment_requirement, sort_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_uid,
                        next_bank_version,
                        question_payload["scenario"],
                        question_payload["question"],
                        question_payload["reference_answer"],
                        question_payload["ability_dimension"],
                        question_payload["scoring_boundary"],
                        question_payload["best_standard"],
                        question_payload["medium_standard"],
                        question_payload["worst_standard"],
                        question_payload["output_format"],
                        question_payload["score_format"],
                        question_payload["comment_requirement"],
                        idx,
                    ),
                )
        conn.commit()

    detail = get_job_detail(job_uid)
    if not detail:
        raise ValueError("job_not_found")
    return detail


def list_jobs(search: str, page: int, page_size: int) -> Dict[str, object]:
    where = ""
    params: List[object] = []
    if search.strip():
        where = "WHERE j.name LIKE ? OR j.job_uid LIKE ?"
        pattern = f"%{search.strip()}%"
        params.extend([pattern, pattern])

    offset = (page - 1) * page_size
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM jobs j {where}",
            params,
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT j.job_uid, j.name, j.created_at, j.question_bank_version,
                   (
                     SELECT COUNT(*)
                     FROM job_questions q
                     WHERE q.job_uid = j.job_uid
                       AND q.bank_version = j.question_bank_version
                   ) AS question_count
            FROM jobs j
            {where}
            ORDER BY j.created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

    items = [
        {
            "job_uid": row["job_uid"],
            "name": row["name"],
            "question_count": int(row["question_count"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


def get_job_detail(job_uid: str) -> Optional[Dict[str, object]]:
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM jobs WHERE job_uid = ?",
            (job_uid,),
        ).fetchone()
        if not job:
            return None
        bank_version = int(job["question_bank_version"] or 1)
        questions = _load_job_questions(conn, job_uid, bank_version=bank_version)

    return {
        "job_uid": job["job_uid"],
        "name": job["name"],
        "duties": job["duties"],
        "requirements": job["requirements"],
        "notes": job["notes"],
        "csv_filename": job["csv_filename"],
        "question_bank_version": bank_version,
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "questions": [
            {
                "id": int(q["id"]),
                "scenario": q["scenario"],
                "question": q["question"],
                "reference_answer": q["reference_answer"],
                "ability_dimension": q["ability_dimension"],
                "scoring_boundary": q["scoring_boundary"],
                "best_standard": q["best_standard"],
                "medium_standard": q["medium_standard"],
                "worst_standard": q["worst_standard"],
                "output_format": q["output_format"],
                "score_format": q["score_format"],
                "comment_requirement": q["comment_requirement"],
                "sort_order": int(q["sort_order"]),
            }
            for q in questions
        ],
    }


def _delete_interview_assets(token: str) -> None:
    for base_dir in (AUDIO_DIR, INTERVIEW_LOG_DIR):
        interview_dir = base_dir / token
        if interview_dir.exists():
            shutil.rmtree(interview_dir, ignore_errors=True)


def delete_job_cascade(job_uid: str) -> bool:
    with get_conn() as conn:
        _begin_immediate(conn)
        row = conn.execute("SELECT job_uid FROM jobs WHERE job_uid = ?", (job_uid,)).fetchone()
        if not row:
            return False
        interview = conn.execute(
            "SELECT token FROM interviews WHERE job_uid = ? LIMIT 1",
            (job_uid,),
        ).fetchone()
        if interview:
            raise ValueError("job_has_interviews")
        conn.execute("DELETE FROM jobs WHERE job_uid = ?", (job_uid,))
        conn.commit()
    return True


def _load_job_questions(
    conn: sqlite3.Connection, job_uid: str, *, bank_version: Optional[int] = None
) -> List[sqlite3.Row]:
    resolved_bank_version = bank_version
    if resolved_bank_version is None:
        row = conn.execute(
            "SELECT question_bank_version FROM jobs WHERE job_uid = ?",
            (job_uid,),
        ).fetchone()
        if not row:
            return []
        resolved_bank_version = int(row["question_bank_version"] or 1)

    return conn.execute(
        """
        SELECT
            id,
            bank_version,
            scenario,
            question,
            reference_answer,
            ability_dimension,
            scoring_boundary,
            best_standard,
            medium_standard,
            worst_standard,
            output_format,
            score_format,
            comment_requirement,
            sort_order
        FROM job_questions
        WHERE job_uid = ? AND bank_version = ?
        ORDER BY sort_order ASC
        """,
        (job_uid, resolved_bank_version),
    ).fetchall()


def _is_interview_active(status: str) -> bool:
    return status in (INTERVIEW_STATUS_PENDING, INTERVIEW_STATUS_IN_PROGRESS)


def _is_interview_terminal(status: str) -> bool:
    return status in (
        INTERVIEW_STATUS_COMPLETED,
        INTERVIEW_STATUS_FAILED,
        INTERVIEW_STATUS_DELETED,
    )


def _resolve_interview_timeout_in_conn(
    conn: sqlite3.Connection,
    token: str,
    now: datetime,
    max_interruptions: int = MAX_INTERRUPTION_COUNT,
) -> Optional[sqlite3.Row]:
    row = conn.execute(
        """
        SELECT token, status, interruption_count, reconnect_deadline_at, expires_at
        FROM interviews
        WHERE token = ?
        """,
        (token,),
    ).fetchone()
    if not row:
        return None

    status = row["status"]
    if _is_interview_active(status) and _is_interview_expired(row["expires_at"], now):
        conn.execute(
            """
            UPDATE interviews
            SET status = ?, reconnect_deadline_at = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_STATUS_FAILED, None, now.isoformat(), token),
        )
        _invalidate_interview_cache(token)
        INTERVIEW_EXPIRY_INDEX.remove(token)
        return conn.execute(
            """
            SELECT token, status, interruption_count, reconnect_deadline_at, expires_at
            FROM interviews
            WHERE token = ?
            """,
            (token,),
        ).fetchone()

    if _is_interview_terminal(status):
        return row

    deadline = parse_iso_or_none(row["reconnect_deadline_at"])
    if not deadline or now < deadline:
        return row

    current_count = int(row["interruption_count"] or 0)
    next_count = current_count + 1
    next_status = (
        INTERVIEW_STATUS_FAILED if next_count >= max_interruptions else INTERVIEW_STATUS_IN_PROGRESS
    )
    conn.execute(
        """
        UPDATE interviews
        SET interruption_count = ?, reconnect_deadline_at = ?, status = ?, updated_at = ?
        WHERE token = ?
        """,
        (next_count, None, next_status, now.isoformat(), token),
    )
    _invalidate_interview_cache(token)
    if next_status == INTERVIEW_STATUS_FAILED:
        INTERVIEW_EXPIRY_INDEX.remove(token)
    return conn.execute(
        """
        SELECT token, status, interruption_count, reconnect_deadline_at, expires_at
        FROM interviews
        WHERE token = ?
        """,
        (token,),
    ).fetchone()


def resolve_interview_timeout(
    token: str,
    max_interruptions: int = MAX_INTERRUPTION_COUNT,
) -> None:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        _resolve_interview_timeout_in_conn(conn, token, now, max_interruptions=max_interruptions)
        conn.commit()


def resolve_all_interview_timeouts(
    max_interruptions: int = MAX_INTERRUPTION_COUNT,
) -> None:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT token
            FROM interviews
            WHERE status IN (?, ?)
            """,
            (INTERVIEW_STATUS_PENDING, INTERVIEW_STATUS_IN_PROGRESS),
        ).fetchall()
        for row in rows:
            _resolve_interview_timeout_in_conn(
                conn,
                row["token"],
                now,
                max_interruptions=max_interruptions,
            )
        conn.commit()


def ensure_interview_expiry_ready() -> bool:
    return INTERVIEW_EXPIRY_INDEX.ensure_ready()


def expire_interview_if_due(token: str, *, now: Optional[datetime] = None) -> bool:
    current_time = now or datetime.now(timezone.utc)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, expires_at FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        if not _is_interview_active(row["status"]):
            return False
        if not _is_interview_expired(row["expires_at"], current_time):
            return False
        conn.execute(
            """
            UPDATE interviews
            SET status = ?, reconnect_deadline_at = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_STATUS_FAILED, None, current_time.isoformat(), token),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    return True


def sweep_expired_interviews(limit: int = INTERVIEW_EXPIRY_SWEEP_BATCH_SIZE) -> Dict[str, int]:
    due_tokens = INTERVIEW_EXPIRY_INDEX.due_tokens(limit=max(1, int(limit)))
    if due_tokens is None:
        raise RuntimeError("interview_expiry_redis_unavailable")
    expired_count = 0
    removed_count = 0
    for token in due_tokens:
        if expire_interview_if_due(token):
            expired_count += 1
        if INTERVIEW_EXPIRY_INDEX.remove(token):
            removed_count += 1
    return {
        "scanned": len(due_tokens),
        "expired": expired_count,
        "removed": removed_count,
    }


def create_interview(
    candidate_name: str,
    job_uid: str,
    notes: Optional[str],
    question_followups: Optional[Sequence[Dict[str, Any]]],
    required_checkins: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    now = utc_now_iso()
    expires_at = _compute_interview_expires_at(now)
    normalized_checkins = normalize_required_checkins(required_checkins)
    serialized_checkins = serialize_required_checkins(normalized_checkins)
    with get_conn() as conn:
        _begin_immediate(conn)
        job = conn.execute(
            "SELECT job_uid, name, question_bank_version FROM jobs WHERE job_uid = ?",
            (job_uid,),
        ).fetchone()
        if not job:
            raise ValueError("job_not_found")

        question_bank_version = int(job["question_bank_version"] or 1)
        bank = _load_job_questions(conn, job_uid, bank_version=question_bank_version)
        if not bank:
            raise ValueError("job_question_bank_empty")

        selected_ids = [int(item["id"]) for item in bank]
        question_count = len(selected_ids)
        (
            normalized_question_followups,
            normalized_question_clarifies,
        ) = _normalize_question_followups(
            question_followups=question_followups,
            question_ids=selected_ids,
        )
        serialized_question_followups = _serialize_question_followups(normalized_question_followups)
        serialized_question_clarifies = _serialize_question_followups(normalized_question_clarifies)

        token = _ensure_unique_interview_token(conn)
        conn.execute(
            """
            INSERT INTO interviews (
                token, candidate_name, job_uid, question_bank_version, duration_minutes, question_count, required_checkins,
                selected_question_ids, question_followup_limits, question_clarify_limits, notes, status, created_at, updated_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                candidate_name,
                job_uid,
                question_bank_version,
                0,
                question_count,
                serialized_checkins,
                ",".join(str(i) for i in selected_ids),
                serialized_question_followups,
                serialized_question_clarifies,
                notes,
                INTERVIEW_STATUS_PENDING,
                now,
                now,
                expires_at,
            ),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    if not INTERVIEW_EXPIRY_INDEX.schedule(token, expires_at):
        with get_conn() as conn:
            conn.execute("DELETE FROM interviews WHERE token = ?", (token,))
            conn.commit()
        _invalidate_interview_cache(token)
        raise RuntimeError("interview_expiry_schedule_failed")

    return {
        "token": token,
        "candidate_name": candidate_name,
        "job_uid": job_uid,
        "job_name": job["name"],
        "question_bank_version": question_bank_version,
        "question_count": question_count,
        "required_checkins": normalized_checkins,
        "question_followups": [
            {
                "question_id": qid,
                "max_followups": normalized_question_followups[qid],
                "max_clarifies": normalized_question_clarifies[qid],
            }
            for qid in selected_ids
        ],
        "notes": notes,
        "status": INTERVIEW_STATUS_PENDING,
        "created_at": now,
        "expires_at": expires_at,
    }


def list_interviews(search: str, page: int, page_size: int) -> Dict[str, object]:
    where = ""
    params: List[object] = []
    if search.strip():
        where = "WHERE i.token LIKE ? OR i.candidate_name LIKE ? OR j.name LIKE ?"
        pattern = f"%{search.strip()}%"
        params.extend([pattern, pattern, pattern])

    offset = (page - 1) * page_size
    with get_conn() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM interviews i
            JOIN jobs j ON j.job_uid = i.job_uid
            {where}
            """,
            params,
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT i.token, i.candidate_name, i.question_count,
                   i.notes, i.status, i.created_at, i.completed_at, i.completed_reason, i.interruption_count,
                   i.expires_at, i.canonical_status, i.canonical_version, i.canonical_source,
                   i.discarded_turn_count, i.echo_risk_flags, i.consistency_flags,
                   (
                     SELECT COUNT(*)
                     FROM interview_attempts a
                     WHERE a.interview_token = i.token
                   ) AS attempt_count,
                   j.job_uid, j.name AS job_name
            FROM interviews i
            JOIN jobs j ON j.job_uid = i.job_uid
            {where}
            ORDER BY i.created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

    items = [
        {
            "token": row["token"],
            "candidate_name": row["candidate_name"],
            "question_count": int(row["question_count"]),
            "notes": row["notes"],
            "status": row["status"],
            "interruption_count": int(row["interruption_count"] or 0),
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "completed_reason": row["completed_reason"],
            "expires_at": row["expires_at"],
            "attempt_count": int(row["attempt_count"] or 0),
            "canonical_status": str(
                row["canonical_status"] or INTERVIEW_CANONICAL_STATUS_READY
            ),
            "canonical_version": int(row["canonical_version"] or 0),
            "canonical_source": str(
                row["canonical_source"] or CANONICAL_SOURCE_TURN_EVENTS_FALLBACK
            ),
            "discarded_turn_count": int(row["discarded_turn_count"] or 0),
            "echo_risk_flags": [
                chunk
                for chunk in str(row["echo_risk_flags"] or "").split(",")
                if chunk.strip()
            ],
            "consistency_flags": [
                chunk
                for chunk in str(row["consistency_flags"] or "").split(",")
                if chunk.strip()
            ],
            "job": {
                "job_uid": row["job_uid"],
                "name": row["job_name"],
            },
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


def get_interview_detail(token: str) -> Optional[Dict[str, object]]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT i.*, j.name AS job_name
            FROM interviews i
            JOIN jobs j ON j.job_uid = i.job_uid
            WHERE i.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None

        turns = conn.execute(
            """
            SELECT role, content, created_at, sort_order
            FROM interview_turns
            WHERE interview_token = ?
            ORDER BY sort_order ASC
            """,
            (token,),
        ).fetchall()
        attempt_count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM interview_attempts WHERE interview_token = ?",
            (token,),
        ).fetchone()
        attempt_count = int(attempt_count_row["c"] or 0) if attempt_count_row else 0

        selected_ids = [
            int(chunk)
            for chunk in (row["selected_question_ids"] or "").split(",")
            if chunk.strip().isdigit()
        ]
        followup_limits = _parse_question_followups(row["question_followup_limits"], selected_ids)
        clarify_limits = _parse_question_clarifies(row["question_clarify_limits"], selected_ids)
        question_rows = _load_job_questions(
            conn,
            row["job_uid"],
            bank_version=int(row["question_bank_version"] or 1),
        )
        by_id = {int(item["id"]): item for item in question_rows}
        selected_questions = []
        for index, qid in enumerate(selected_ids):
            question = by_id.get(qid)
            if not question:
                continue
            selected_questions.append(
                {
                    "sort_order": index + 1,
                    "question_id": qid,
                    "scenario": str(question["scenario"] or "").strip(),
                    "question": question["question"],
                    "max_followups": followup_limits.get(qid, DEFAULT_QUESTION_MAX_FOLLOWUPS),
                    "max_clarifies": clarify_limits.get(qid, DEFAULT_QUESTION_MAX_CLARIFIES),
                }
            )
        scorecard = _load_interview_scorecard_in_conn(conn, token)

    return {
        "token": row["token"],
        "candidate_name": row["candidate_name"],
        "question_count": int(row["question_count"]),
        "notes": row["notes"],
        "status": row["status"],
        "interruption_count": int(row["interruption_count"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
        "completed_at": row["completed_at"],
        "completed_reason": row["completed_reason"],
        "reconnect_deadline_at": row["reconnect_deadline_at"],
        "attempt_count": attempt_count,
        "canonical_status": str(
            row["canonical_status"] or INTERVIEW_CANONICAL_STATUS_READY
        ),
        "canonical_version": int(row["canonical_version"] or 0),
        "canonical_source": str(
            row["canonical_source"] or CANONICAL_SOURCE_TURN_EVENTS_FALLBACK
        ),
        "discarded_turn_count": int(row["discarded_turn_count"] or 0),
        "echo_risk_flags": [
            chunk
            for chunk in str(row["echo_risk_flags"] or "").split(",")
            if chunk.strip()
        ],
        "consistency_flags": [
            chunk
            for chunk in str(row["consistency_flags"] or "").split(",")
            if chunk.strip()
        ],
        "candidate_audio_path": row["candidate_audio_path"],
        "interviewer_audio_path": row["interviewer_audio_path"],
        "job": {
            "job_uid": row["job_uid"],
            "name": row["job_name"],
        },
        "question_bank_version": int(row["question_bank_version"] or 1),
        "selected_questions": selected_questions,
        "required_checkins": parse_required_checkins(row["required_checkins"]),
        "scorecard": scorecard,
        "turns": [
            {
                "role": t["role"],
                "content": t["content"],
                "created_at": t["created_at"],
                "sort_order": int(t["sort_order"]),
            }
            for t in turns
        ],
    }


def delete_interview(token: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT token FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM interviews WHERE token = ?", (token,))
        conn.commit()
    _invalidate_interview_cache(token)
    INTERVIEW_EXPIRY_INDEX.remove(token)

    _delete_interview_assets(token)
    return True


def get_public_access(token: str) -> Optional[Dict[str, object]]:
    cached = INTERVIEW_CACHE.get_public_access(token)
    if cached is not CACHE_MISS:
        return cached

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT i.token, i.candidate_name, i.status, i.interruption_count,
                   i.expires_at,
                   i.required_checkins,
                   j.job_uid, j.name AS job_name
            FROM interviews i
            JOIN jobs j ON j.job_uid = i.job_uid
            WHERE i.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            INTERVIEW_CACHE.set_public_access(token, None, expires_at=None)
            return None
        if not _is_interview_active(row["status"]):
            INTERVIEW_CACHE.set_public_access(token, None, expires_at=row["expires_at"])
            return None
        if _is_interview_expired(row["expires_at"], datetime.now(timezone.utc)):
            INTERVIEW_CACHE.set_public_access(token, None, expires_at=row["expires_at"])
            return None
        payload = {
            "token": row["token"],
            "candidate_name": row["candidate_name"],
            "status": row["status"],
            "interruption_count": int(row["interruption_count"] or 0),
            "required_checkins": parse_required_checkins(row["required_checkins"]),
            "job": {
                "job_uid": row["job_uid"],
                "name": row["job_name"],
            },
        }
        INTERVIEW_CACHE.set_public_access(token, payload, expires_at=row["expires_at"])
        return payload


def interview_exists(token: str) -> bool:
    if not token:
        return False
    cached = INTERVIEW_CACHE.get_exists(token)
    if cached is not None:
        return cached

    with get_conn() as conn:
        row = conn.execute(
            "SELECT token, expires_at FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
    exists = bool(
        row
        and not _is_interview_expired(
            row["expires_at"],
            datetime.now(timezone.utc),
        )
    )
    INTERVIEW_CACHE.set_exists(token, exists, expires_at=row["expires_at"] if row else None)
    return exists


def _keywords_from_reference(reference: str) -> List[str]:
    parts = []
    for chunk in (
        reference.replace("，", " ")
        .replace("。", " ")
        .replace(";", " ")
        .replace("；", " ")
        .replace(",", " ")
        .split()
    ):
        word = chunk.strip()
        if len(word) >= 2:
            parts.append(word)
        if len(parts) >= 4:
            break
    return parts


def _build_question_evidence(question_row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "must_cover": _keywords_from_reference(question_row["reference_answer"]),
        "scenario": question_row["scenario"],
        "ability_dimension": question_row["ability_dimension"],
        "scoring_boundary": question_row["scoring_boundary"],
        "best_standard": question_row["best_standard"],
        "medium_standard": question_row["medium_standard"],
        "worst_standard": question_row["worst_standard"],
        "output_format": question_row["output_format"],
        "score_format": question_row["score_format"],
        "comment_requirement": question_row["comment_requirement"],
    }


def start_interview_session(token: str) -> Optional[InterviewSessionData]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT i.token, i.candidate_name, i.job_uid, i.status, i.selected_question_ids,
                   i.question_bank_version, i.question_followup_limits, i.question_clarify_limits, i.reconnect_deadline_at, i.expires_at,
                   j.name AS job_name
            FROM interviews i
            JOIN jobs j ON j.job_uid = i.job_uid
            WHERE i.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        if not _is_interview_active(row["status"]):
            return None
        if _is_interview_expired(row["expires_at"], datetime.now(timezone.utc)):
            return None

        selected_ids = [
            int(chunk)
            for chunk in (row["selected_question_ids"] or "").split(",")
            if chunk.strip().isdigit()
        ]
        followup_limits = _parse_question_followups(row["question_followup_limits"], selected_ids)
        clarify_limits = _parse_question_clarifies(row["question_clarify_limits"], selected_ids)
        questions = _load_job_questions(
            conn,
            row["job_uid"],
            bank_version=int(row["question_bank_version"] or 1),
        )
        by_id = {int(item["id"]): item for item in questions}

        selected_questions = []
        for qid in selected_ids:
            q = by_id.get(qid)
            if not q:
                continue
            selected_questions.append(
                {
                    "question_id": f"q{qid}",
                    "scenario": str(q["scenario"] or "").strip(),
                    "main_question": q["question"],
                    "evidence": _build_question_evidence(q),
                    "max_followups": followup_limits.get(qid, DEFAULT_QUESTION_MAX_FOLLOWUPS),
                    "max_clarifies": clarify_limits.get(qid, DEFAULT_QUESTION_MAX_CLARIFIES),
                }
            )

        if not selected_questions:
            for q in questions[:1]:
                selected_questions.append(
                    {
                        "question_id": f"q{int(q['id'])}",
                        "scenario": str(q["scenario"] or "").strip(),
                        "main_question": q["question"],
                        "evidence": _build_question_evidence(q),
                        "max_followups": followup_limits.get(
                            int(q["id"]), DEFAULT_QUESTION_MAX_FOLLOWUPS
                        ),
                        "max_clarifies": clarify_limits.get(
                            int(q["id"]), DEFAULT_QUESTION_MAX_CLARIFIES
                        ),
                    }
                )

    return InterviewSessionData(
        token=row["token"],
        candidate_name=row["candidate_name"],
        job_uid=row["job_uid"],
        job_name=row["job_name"],
        status=row["status"],
        questions=selected_questions,
    )


def mark_interview_in_progress(token: str) -> bool:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, expires_at FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        if not _is_interview_active(row["status"]):
            return False
        if _is_interview_expired(row["expires_at"], now):
            return False
        conn.execute(
            """
            UPDATE interviews
            SET status = ?, reconnect_deadline_at = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_STATUS_IN_PROGRESS, None, now.isoformat(), token),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    return True


def start_interview_attempt(
    token: str,
    attempt_id: str,
    *,
    client_id: str = "",
    owner_id: str = "",
    started_at: Optional[str] = None,
) -> Optional[int]:
    normalized_attempt_id = str(attempt_id or "").strip()
    if not token or not normalized_attempt_id:
        return None
    now_iso = (started_at or "").strip() or utc_now_iso()
    normalized_client_id = str(client_id or "").strip()
    normalized_owner_id = str(owner_id or "").strip()
    with get_conn() as conn:
        _begin_immediate(conn)
        row = conn.execute(
            "SELECT status, expires_at FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        if not _is_interview_active(str(row["status"] or "")):
            return None
        if _is_interview_expired(row["expires_at"], datetime.now(timezone.utc)):
            return None
        existing = conn.execute(
            "SELECT attempt_seq FROM interview_attempts WHERE attempt_id = ?",
            (normalized_attempt_id,),
        ).fetchone()
        if existing:
            conn.commit()
            return int(existing["attempt_seq"] or 0)
        next_seq_row = conn.execute(
            "SELECT COALESCE(MAX(attempt_seq), 0) + 1 AS next_seq "
            "FROM interview_attempts WHERE interview_token = ?",
            (token,),
        ).fetchone()
        attempt_seq = int(next_seq_row["next_seq"] or 1)
        conn.execute(
            """
            INSERT INTO interview_attempts (
                attempt_id, interview_token, client_id, owner_id, attempt_seq,
                status, started_at, close_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_attempt_id,
                token,
                normalized_client_id,
                normalized_owner_id,
                attempt_seq,
                INTERVIEW_STATUS_IN_PROGRESS,
                now_iso,
                "",
            ),
        )
        conn.execute(
            """
            UPDATE interviews
            SET canonical_status = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_CANONICAL_STATUS_BUILDING, now_iso, token),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    return attempt_seq


def close_interview_attempt(
    attempt_id: str,
    *,
    status: str,
    close_source: str,
    ended_at: Optional[str] = None,
) -> bool:
    normalized_attempt_id = str(attempt_id or "").strip()
    if not normalized_attempt_id:
        return False
    ended_at_iso = (ended_at or "").strip() or utc_now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT interview_token FROM interview_attempts WHERE attempt_id = ?",
            (normalized_attempt_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            UPDATE interview_attempts
            SET status = ?, ended_at = ?, close_source = ?
            WHERE attempt_id = ?
            """,
            (
                str(status or INTERVIEW_STATUS_IN_PROGRESS),
                ended_at_iso,
                str(close_source or "").strip(),
                normalized_attempt_id,
            ),
        )
        conn.commit()
        token = str(row["interview_token"] or "")
    if token:
        _invalidate_interview_cache(token)
    return True


def save_interview_turn_events(
    token: str, attempt_id: str, events: Sequence[Dict[str, Any]]
) -> int:
    normalized_attempt_id = str(attempt_id or "").strip()
    if not token or not normalized_attempt_id:
        return 0
    inserted = 0
    with get_conn() as conn:
        for idx, item in enumerate(events):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            text = str(item.get("text", "") or "").strip()
            if role not in {"candidate", "interviewer"} or not text:
                continue
            raw_seq_no = item.get("seq_no")
            try:
                seq_no = int(raw_seq_no)
            except (TypeError, ValueError):
                seq_no = idx + 1
            seq_no = max(1, seq_no)
            event_ts = str(item.get("event_ts", "") or "").strip() or utc_now_iso()
            event_kind = str(item.get("event_kind", "transcript") or "transcript").strip()
            if not event_kind:
                event_kind = "transcript"
            question_id = str(item.get("question_id", "") or "").strip()
            raw_question_index = item.get("question_index", 0)
            try:
                question_index = int(raw_question_index or 0)
            except (TypeError, ValueError):
                question_index = 0
            question_index = max(0, question_index)
            raw_question_epoch = item.get("question_epoch", 0)
            try:
                question_epoch = int(raw_question_epoch or 0)
            except (TypeError, ValueError):
                question_epoch = 0
            question_epoch = max(0, question_epoch)
            commit_state = str(item.get("commit_state", "") or "").strip()
            conn.execute(
                """
                INSERT INTO interview_turn_events (
                    attempt_id, interview_token, seq_no, event_ts, role, text,
                    event_kind, question_id, question_index, question_epoch, commit_state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id, seq_no) DO UPDATE SET
                    event_ts = excluded.event_ts,
                    role = excluded.role,
                    text = excluded.text,
                    event_kind = excluded.event_kind,
                    question_id = excluded.question_id,
                    question_index = excluded.question_index,
                    question_epoch = excluded.question_epoch,
                    commit_state = excluded.commit_state
                """,
                (
                    normalized_attempt_id,
                    token,
                    seq_no,
                    event_ts,
                    role,
                    text,
                    event_kind,
                    question_id,
                    question_index,
                    question_epoch,
                    commit_state,
                ),
            )
            inserted += 1
        conn.commit()
    _invalidate_interview_cache(token)
    return inserted


def save_interview_checkpoint_journal(
    token: str,
    attempt_id: str,
    checkpoint_seq: int,
    checkpoint: Dict[str, Any],
    *,
    source: str = "flush",
    checkpoint_ts: Optional[str] = None,
) -> bool:
    normalized_attempt_id = str(attempt_id or "").strip()
    if not token or not normalized_attempt_id:
        return False
    if not isinstance(checkpoint, dict):
        return False
    try:
        normalized_seq = max(1, int(checkpoint_seq))
    except (TypeError, ValueError):
        return False
    payload_json = json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"))
    normalized_ts = str(checkpoint_ts or "").strip() or utc_now_iso()
    normalized_source = str(source or "").strip()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_checkpoint_journal (
                interview_token, attempt_id, checkpoint_seq, checkpoint_ts,
                payload_json, source
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(attempt_id, checkpoint_seq) DO UPDATE SET
                checkpoint_ts = excluded.checkpoint_ts,
                payload_json = excluded.payload_json,
                source = excluded.source
            """,
            (
                token,
                normalized_attempt_id,
                normalized_seq,
                normalized_ts,
                payload_json,
                normalized_source,
            ),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    return True


def _normalize_turn_text_for_dedupe(text: str) -> str:
    collapsed = "".join(str(text or "").strip().split())
    return collapsed.lower()


def _dedupe_turn_event_rows(
    rows: Sequence[sqlite3.Row], *, window_seconds: int = 12
) -> Tuple[List[Dict[str, Any]], int]:
    dedupe_window = max(0, int(window_seconds))
    canonical: List[Dict[str, Any]] = []
    latest_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    dropped = 0

    for row in rows:
        role = str(row["role"] or "").strip()
        text = str(row["text"] or "").strip()
        if role not in {"candidate", "interviewer"} or not text:
            continue
        question_id = str(row["question_id"] or "").strip()
        question_index = int(row["question_index"] or 0)
        attempt_seq = int(row["attempt_seq"] or 0)
        event_ts = str(row["event_ts"] or "").strip() or utc_now_iso()
        event_dt = parse_iso_or_none(event_ts)
        qkey = question_id or (f"qidx:{question_index}" if question_index > 0 else "-")
        key = (role, qkey, _normalize_turn_text_for_dedupe(text))
        previous = latest_by_key.get(key)
        if previous:
            prev_dt = previous["event_dt"]
            within_window = False
            if event_dt and prev_dt:
                within_window = abs((event_dt - prev_dt).total_seconds()) <= dedupe_window
            if within_window:
                if attempt_seq >= int(previous["attempt_seq"] or 0):
                    canonical[int(previous["index"])] = {
                        "role": role,
                        "content": text,
                        "created_at": event_ts,
                        "question_id": question_id,
                        "question_index": question_index,
                        "attempt_seq": attempt_seq,
                        "event_dt": event_dt,
                    }
                    latest_by_key[key] = {
                        "index": int(previous["index"]),
                        "attempt_seq": attempt_seq,
                        "event_dt": event_dt,
                    }
                dropped += 1
                continue
        canonical.append(
            {
                "role": role,
                "content": text,
                "created_at": event_ts,
                "question_id": question_id,
                "question_index": question_index,
                "attempt_seq": attempt_seq,
                "event_dt": event_dt,
            }
        )
        latest_by_key[key] = {
            "index": len(canonical) - 1,
            "attempt_seq": attempt_seq,
            "event_dt": event_dt,
        }
    return canonical, dropped


def _load_selected_question_blueprint_in_conn(
    conn: sqlite3.Connection, token: str
) -> List[Dict[str, Any]]:
    interview_row = conn.execute(
        """
        SELECT token, job_uid, question_bank_version, selected_question_ids
        FROM interviews
        WHERE token = ?
        """,
        (token,),
    ).fetchone()
    if not interview_row:
        return []
    selected_ids = [
        int(chunk)
        for chunk in (interview_row["selected_question_ids"] or "").split(",")
        if chunk.strip().isdigit()
    ]
    if not selected_ids:
        return []
    question_rows = _load_job_questions(
        conn,
        interview_row["job_uid"],
        bank_version=int(interview_row["question_bank_version"] or 1),
    )
    by_id = {int(item["id"]): item for item in question_rows}
    selected_questions: List[Dict[str, Any]] = []
    for idx, qid in enumerate(selected_ids):
        q = by_id.get(qid)
        if not q:
            continue
        evidence = _build_question_evidence(q)
        selected_questions.append(
            {
                "sort_order": idx + 1,
                "question_id": f"q{qid}",
                "scenario": str(q["scenario"] or "").strip(),
                "question": str(q["question"] or "").strip(),
                "evidence": evidence,
                "ability_dimension": str(evidence.get("ability_dimension", "") or "").strip(),
                "scoring_boundary": str(evidence.get("scoring_boundary", "") or "").strip(),
                "score_format": str(evidence.get("score_format", "") or "").strip(),
                "comment_requirement": str(
                    evidence.get("comment_requirement", "") or ""
                ).strip(),
            }
        )
    return selected_questions


def build_score_inputs_from_canonical_turns(
    token: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
    canonical_events: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    owned_conn = conn is None
    working_conn = conn or get_conn()
    try:
        selected_questions = _load_selected_question_blueprint_in_conn(working_conn, token)
        if not selected_questions:
            return []
        turns = working_conn.execute(
            """
            SELECT role, content, created_at
            FROM interview_turns
            WHERE interview_token = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (token,),
        ).fetchall()
        question_answers: Dict[str, List[str]] = {
            str(item["question_id"]): [] for item in selected_questions
        }
        question_id_order = [str(item["question_id"]) for item in selected_questions]
        index_to_question_id = {
            idx + 1: qid for idx, qid in enumerate(question_id_order)
        }

        if canonical_events is not None:
            for event in canonical_events:
                if not isinstance(event, dict):
                    continue
                role = str(event.get("role", "") or "").strip()
                if role != "candidate":
                    continue
                text = str(event.get("content", "") or "").strip()
                if not text:
                    continue
                question_id = str(event.get("question_id", "") or "").strip()
                if not question_id:
                    try:
                        raw_index = int(event.get("question_index", 0) or 0)
                    except (TypeError, ValueError):
                        raw_index = 0
                    question_id = index_to_question_id.get(raw_index, "")
                if question_id in question_answers:
                    question_answers[question_id].append(text)
        else:
            events = working_conn.execute(
                """
                SELECT role, text, question_id, question_index, event_ts
                FROM interview_turn_events
                WHERE interview_token = ?
                ORDER BY event_ts ASC, id ASC
                """,
                (token,),
            ).fetchall()
            for row in events:
                role = str(row["role"] or "").strip()
                if role != "candidate":
                    continue
                text = str(row["text"] or "").strip()
                if not text:
                    continue
                question_id = str(row["question_id"] or "").strip()
                if not question_id:
                    raw_index = int(row["question_index"] or 0)
                    question_id = index_to_question_id.get(raw_index, "")
                if question_id in question_answers:
                    question_answers[question_id].append(text)

        if canonical_events is None and question_id_order and not any(question_answers.values()):
            for row in turns:
                if str(row["role"] or "").strip() != "candidate":
                    continue
                text = str(row["content"] or "").strip()
                if not text:
                    continue
                question_answers[question_id_order[-1]].append(text)

        segments: List[Dict[str, Any]] = []
        previous_scene = ""
        for item in selected_questions:
            scene = str(item["scenario"] or "").strip()
            start_new_segment = False
            if scene:
                start_new_segment = (not segments) or (scene != previous_scene)
            else:
                start_new_segment = True
            if start_new_segment:
                segments.append(
                    {
                        "scene": scene,
                        "questions": [],
                        "candidate_answers": [],
                        "scoring_boundary": "",
                        "score_format": "",
                        "ability_dimension": "",
                        "comment_requirement": "",
                    }
                )
            segment = segments[-1]
            segment["questions"].append(str(item["question"] or "").strip())
            segment["candidate_answers"].extend(
                question_answers.get(str(item["question_id"]), [])
            )
            if not segment["scoring_boundary"]:
                segment["scoring_boundary"] = str(item["scoring_boundary"] or "").strip()
            if not segment["score_format"]:
                segment["score_format"] = str(item["score_format"] or "").strip()
            if not segment["ability_dimension"]:
                segment["ability_dimension"] = str(item["ability_dimension"] or "").strip()
            if not segment["comment_requirement"]:
                segment["comment_requirement"] = str(
                    item["comment_requirement"] or ""
                ).strip()
            previous_scene = scene

        score_inputs: List[Dict[str, Any]] = []
        for idx, segment in enumerate(segments, start=1):
            questions = [q for q in segment["questions"] if str(q or "").strip()]
            answers = [
                str(answer or "").strip()
                for answer in segment["candidate_answers"]
                if str(answer or "").strip()
            ]
            scene = str(segment.get("scene", "") or "").strip()
            if scene:
                question_text = f"[场景] {scene}\n" + "\n".join(
                    f"{order + 1}. {q}" for order, q in enumerate(questions)
                )
            else:
                question_text = "\n".join(questions)
            score_inputs.append(
                {
                    "question_id": f"scene-{idx}",
                    "question_index": idx,
                    "sort_order": idx,
                    "question": question_text.strip(),
                    "ability_dimension": str(
                        segment.get("ability_dimension", "") or ""
                    ).strip(),
                    "scoring_boundary": str(
                        segment.get("scoring_boundary", "") or ""
                    ).strip(),
                    "best_standard": "",
                    "medium_standard": "",
                    "worst_standard": "",
                    "score_format": str(segment.get("score_format", "") or "").strip(),
                    "comment_requirement": str(
                        segment.get("comment_requirement", "") or ""
                    ).strip(),
                    "candidate_answers": answers,
                    "aggregated_answer": "\n".join(answers).strip(),
                }
            )
        return score_inputs
    finally:
        if owned_conn:
            working_conn.close()


def _load_latest_checkpoint_payload_in_conn(
    conn: sqlite3.Connection,
    token: str,
) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT j.payload_json, j.source, j.attempt_id, j.checkpoint_seq, j.checkpoint_ts,
               a.attempt_seq
        FROM interview_checkpoint_journal j
        LEFT JOIN interview_attempts a ON a.attempt_id = j.attempt_id
        WHERE j.interview_token = ?
        ORDER BY COALESCE(a.attempt_seq, 0) DESC, j.checkpoint_seq DESC, j.id DESC
        """,
        (token,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or ""))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        return {
            "payload": payload,
            "source": str(row["source"] or "").strip(),
            "attempt_id": str(row["attempt_id"] or "").strip(),
            "attempt_seq": int(row["attempt_seq"] or 0),
            "checkpoint_seq": int(row["checkpoint_seq"] or 0),
            "checkpoint_ts": str(row["checkpoint_ts"] or "").strip(),
        }
    return None


def _load_latest_attempt_bookend_turns_in_conn(
    conn: sqlite3.Connection,
    token: str,
) -> List[Dict[str, Any]]:
    attempt_row = conn.execute(
        """
        SELECT attempt_id, attempt_seq
        FROM interview_attempts
        WHERE interview_token = ?
        ORDER BY attempt_seq DESC, id DESC
        LIMIT 1
        """,
        (token,),
    ).fetchone()
    if not attempt_row:
        return []
    attempt_id = str(attempt_row["attempt_id"] or "").strip()
    attempt_seq = int(attempt_row["attempt_seq"] or 0)
    if not attempt_id:
        return []
    rows = conn.execute(
        """
        SELECT role, text, event_ts
        FROM interview_turn_events
        WHERE interview_token = ? AND attempt_id = ?
          AND role = 'interviewer'
          AND COALESCE(question_index, 0) <= 0
        ORDER BY event_ts ASC, id ASC
        """,
        (token, attempt_id),
    ).fetchall()
    events: List[Dict[str, Any]] = []
    last_key = ""
    for row in rows:
        text = str(row["text"] or "").strip()
        if not text:
            continue
        normalized_key = _normalize_turn_text_for_dedupe(text)
        if normalized_key and normalized_key == last_key:
            continue
        last_key = normalized_key
        event_ts = str(row["event_ts"] or "").strip() or utc_now_iso()
        events.append(
            {
                "role": "interviewer",
                "content": text,
                "created_at": event_ts,
                "question_id": "",
                "question_index": 0,
                "question_epoch": 0,
                "attempt_seq": attempt_seq,
            }
        )
    if len(events) <= 1:
        return events
    first = events[0]
    last = events[-1]
    if _normalize_turn_text_for_dedupe(first["content"]) == _normalize_turn_text_for_dedupe(
        last["content"]
    ):
        return [first]
    return [first, last]


def _build_canonical_events_from_checkpoint(
    *,
    payload: Dict[str, Any],
    selected_questions: Sequence[Dict[str, Any]],
    attempt_seq: int,
    checkpoint_ts: str,
    intro_wrap_events: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        return []
    question_by_id: Dict[str, Dict[str, Any]] = {}
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or "").strip()
        if not question_id:
            continue
        question_by_id[question_id] = item

    canonical_events: List[Dict[str, Any]] = []
    bookend_first: Optional[Dict[str, Any]] = None
    bookend_last: Optional[Dict[str, Any]] = None
    if intro_wrap_events:
        normalized_events = [
            event for event in intro_wrap_events if isinstance(event, dict)
        ]
        if normalized_events:
            first = normalized_events[0]
            bookend_first = {
                "role": str(first.get("role", "") or "").strip(),
                "content": str(first.get("content", "") or "").strip(),
                "created_at": str(first.get("created_at", "") or "").strip()
                or checkpoint_ts
                or utc_now_iso(),
                "question_id": "",
                "question_index": 0,
                "question_epoch": 0,
                "attempt_seq": attempt_seq,
            }
            if len(normalized_events) > 1:
                last = normalized_events[-1]
                bookend_last = {
                    "role": str(last.get("role", "") or "").strip(),
                    "content": str(last.get("content", "") or "").strip(),
                    "created_at": str(last.get("created_at", "") or "").strip()
                    or checkpoint_ts
                    or utc_now_iso(),
                    "question_id": "",
                    "question_index": 0,
                    "question_epoch": 0,
                    "attempt_seq": attempt_seq,
                }
                if _normalize_turn_text_for_dedupe(bookend_first["content"]) == _normalize_turn_text_for_dedupe(
                    bookend_last["content"]
                ):
                    bookend_last = None

    if bookend_first and bookend_first["content"]:
        canonical_events.append(bookend_first)

    for selected in selected_questions:
        question_id = str(selected.get("question_id", "") or "").strip()
        if not question_id:
            continue
        question_index = int(selected.get("sort_order", 0) or 0)
        raw_question = question_by_id.get(question_id) or {}
        raw_turns = raw_question.get("turns")
        if not isinstance(raw_turns, list):
            continue
        try:
            question_epoch = max(0, int(raw_question.get("question_epoch", 0) or 0))
        except (TypeError, ValueError):
            question_epoch = 0
        for turn in raw_turns:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "") or "").strip()
            content = str(turn.get("content", "") or "").strip()
            if role not in {"candidate", "interviewer"} or not content:
                continue
            created_at = str(turn.get("created_at", "") or "").strip() or checkpoint_ts
            canonical_events.append(
                {
                    "role": role,
                    "content": content,
                    "created_at": created_at or utc_now_iso(),
                    "question_id": question_id,
                    "question_index": max(0, question_index),
                    "question_epoch": question_epoch,
                    "attempt_seq": attempt_seq,
                }
            )
    if bookend_last and bookend_last["content"]:
        canonical_events.append(bookend_last)
    return canonical_events


def _find_questions_missing_candidate_answer_in_checkpoint(
    payload: Dict[str, Any],
    selected_questions: Sequence[Dict[str, Any]],
) -> List[str]:
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        return []
    question_by_id: Dict[str, Dict[str, Any]] = {}
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("question_id", "") or "").strip()
        if not question_id:
            continue
        question_by_id[question_id] = item

    missing: List[str] = []
    for selected in selected_questions:
        question_id = str(selected.get("question_id", "") or "").strip()
        if not question_id:
            continue
        raw_question = question_by_id.get(question_id) or {}
        raw_turns = raw_question.get("turns")
        if not isinstance(raw_turns, list):
            continue
        interviewer_asked = False
        candidate_answered = False
        for turn in raw_turns:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "") or "").strip()
            content = str(turn.get("content", "") or "").strip()
            if role not in {"candidate", "interviewer"} or not content:
                continue
            if role == "interviewer":
                interviewer_asked = True
            elif role == "candidate":
                candidate_answered = True
        if interviewer_asked and not candidate_answered:
            missing.append(question_id)
    return missing


def _build_canonical_events_from_turn_events(
    conn: sqlite3.Connection,
    token: str,
    *,
    selected_questions: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    question_id_set = {
        str(item.get("question_id", "") or "").strip() for item in selected_questions
    }
    index_to_question_id = {
        int(item.get("sort_order", 0) or 0): str(item.get("question_id", "") or "").strip()
        for item in selected_questions
        if int(item.get("sort_order", 0) or 0) > 0
    }
    rows = conn.execute(
        """
        SELECT role, text, event_ts, question_id, question_index, question_epoch, id
        FROM interview_turn_events
        WHERE interview_token = ?
          AND role IN ('candidate', 'interviewer')
          AND TRIM(text) != ''
        ORDER BY event_ts ASC, id ASC
        """,
        (token,),
    ).fetchall()
    events: List[Dict[str, Any]] = []
    for row in rows:
        role = str(row["role"] or "").strip()
        text = str(row["text"] or "").strip()
        if role not in {"candidate", "interviewer"} or not text:
            continue
        question_id = str(row["question_id"] or "").strip()
        try:
            question_index = int(row["question_index"] or 0)
        except (TypeError, ValueError):
            question_index = 0
        if not question_id and question_index > 0:
            question_id = index_to_question_id.get(question_index, "")
        if question_id and question_id not in question_id_set:
            question_id = ""
            question_index = 0
        try:
            question_epoch = int(row["question_epoch"] or 0)
        except (TypeError, ValueError):
            question_epoch = 0
        event_ts = str(row["event_ts"] or "").strip() or utc_now_iso()
        events.append(
            {
                "role": role,
                "content": text,
                "created_at": event_ts,
                "question_id": question_id,
                "question_index": max(0, question_index),
                "question_epoch": max(0, question_epoch),
                "attempt_seq": 0,
            }
        )
    return events


def _compute_echo_risk_flags(
    canonical_events: Sequence[Dict[str, Any]],
    *,
    window_seconds: int = 20,
    ratio_threshold: float = 0.9,
) -> List[str]:
    recent_interviewer: List[Tuple[Optional[datetime], str]] = []
    for event in canonical_events:
        if not isinstance(event, dict):
            continue
        role = str(event.get("role", "") or "").strip()
        text = str(event.get("content", "") or "").strip()
        if not text:
            continue
        normalized = _normalize_turn_text_for_dedupe(text)
        if not normalized:
            continue
        event_dt = parse_iso_or_none(str(event.get("created_at", "") or ""))
        if role == "interviewer":
            recent_interviewer.append((event_dt, normalized))
            if len(recent_interviewer) > 16:
                recent_interviewer = recent_interviewer[-16:]
            continue
        if role != "candidate":
            continue
        for interviewer_dt, interviewer_text in reversed(recent_interviewer):
            if event_dt and interviewer_dt:
                delta = (event_dt - interviewer_dt).total_seconds()
                if delta < 0:
                    continue
                if delta > max(1, int(window_seconds)):
                    break
            similarity = difflib.SequenceMatcher(
                None,
                normalized,
                interviewer_text,
            ).ratio()
            if similarity >= ratio_threshold:
                return ["candidate_echo_suspected"]
            if len(normalized) >= 10 and normalized in interviewer_text:
                return ["candidate_echo_suspected"]
            if len(interviewer_text) >= 10 and interviewer_text in normalized:
                return ["candidate_echo_suspected"]
    return []


def _merge_wav_files(paths: Sequence[Path], output_path: Path) -> bool:
    if not paths:
        return False
    try:
        with wave.open(str(paths[0]), "rb") as first:
            params = (
                first.getnchannels(),
                first.getsampwidth(),
                first.getframerate(),
            )
        with wave.open(str(output_path), "wb") as out_wav:
            out_wav.setnchannels(params[0])
            out_wav.setsampwidth(params[1])
            out_wav.setframerate(params[2])
            for path in paths:
                with wave.open(str(path), "rb") as src:
                    current = (
                        src.getnchannels(),
                        src.getsampwidth(),
                        src.getframerate(),
                    )
                    if current != params:
                        return False
                    out_wav.writeframes(src.readframes(src.getnframes()))
    except Exception:
        return False
    return True


def _merge_mp3_files(paths: Sequence[Path], output_path: Path) -> bool:
    if not paths:
        return False
    try:
        with output_path.open("wb") as out_file:
            for path in paths:
                out_file.write(path.read_bytes())
    except Exception:
        return False
    return True


def _copy_or_merge_audio_track_rows(
    rows: Sequence[sqlite3.Row], *, token: str, track: str, flags: List[str]
) -> Optional[str]:
    existing_rows = []
    for row in rows:
        raw_path = str(row["file_path"] or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            continue
        existing_rows.append((row, path))
    if not existing_rows:
        return None

    canonical_dir = AUDIO_DIR / token / "canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)

    paths = [path for _, path in existing_rows]
    suffixes = {path.suffix.lower() for path in paths}
    suffix = paths[0].suffix.lower() if paths else ""
    output_suffix = suffix if suffix else ".raw"
    output_path = canonical_dir / f"{track}{output_suffix}"

    if len(paths) == 1:
        shutil.copyfile(paths[0], output_path)
        return str(output_path)

    if len(suffixes) == 1 and output_suffix == ".wav":
        if _merge_wav_files(paths, output_path):
            return str(output_path)
    elif len(suffixes) == 1 and output_suffix == ".mp3":
        if _merge_mp3_files(paths, output_path):
            return str(output_path)

    fallback_row, fallback_path = existing_rows[-1]
    shutil.copyfile(fallback_path, output_path)
    flags.append(f"{track}_merge_fallback")
    if len(suffixes) > 1:
        flags.append(f"{track}_mixed_format")
    if int(fallback_row["attempt_seq"] or 0) <= 0:
        flags.append(f"{track}_attempt_seq_missing")
    return str(output_path)


def finalize_canonical_artifacts(
    token: str,
    *,
    completed_reason: Optional[str] = None,
) -> Dict[str, Any]:
    now_iso = utc_now_iso()
    failed_consistency_flags: List[str] = []
    try:
        with get_conn() as conn:
            _begin_immediate(conn)
            interview_row = conn.execute(
                """
                SELECT token, candidate_name
                FROM interviews
                WHERE token = ?
                """,
                (token,),
            ).fetchone()
            if not interview_row:
                return {"ok": False, "error": "interview_not_found"}

            conn.execute(
                """
                UPDATE interviews
                SET canonical_status = ?, updated_at = ?
                WHERE token = ?
                """,
                (INTERVIEW_CANONICAL_STATUS_BUILDING, now_iso, token),
            )

            attempts = conn.execute(
                """
                SELECT attempt_id, attempt_seq
                FROM interview_attempts
                WHERE interview_token = ?
                ORDER BY attempt_seq ASC, id ASC
                """,
                (token,),
            ).fetchall()
            attempt_count = len(attempts)
            selected_questions = _load_selected_question_blueprint_in_conn(conn, token)
            consistency_flags: List[str] = []
            canonical_source = CANONICAL_SOURCE_TURN_EVENTS_FALLBACK
            canonical_events: List[Dict[str, Any]] = []
            partial_mode = (
                str(completed_reason or "").strip() in BEST_EFFORT_COMPLETED_REASONS
            )

            def _fail_with_flag(flag: str) -> None:
                normalized = str(flag or "").strip()
                if normalized:
                    consistency_flags.append(normalized)
                failed_consistency_flags.clear()
                failed_consistency_flags.extend(sorted(set(consistency_flags)))
                raise RuntimeError(normalized or "finalize_failed")

            checkpoint_meta = _load_latest_checkpoint_payload_in_conn(conn, token)
            if checkpoint_meta:
                canonical_source = CANONICAL_SOURCE_CHECKPOINT
                intro_wrap_events = _load_latest_attempt_bookend_turns_in_conn(conn, token)
                canonical_events = _build_canonical_events_from_checkpoint(
                    payload=checkpoint_meta["payload"],
                    selected_questions=selected_questions,
                    attempt_seq=int(checkpoint_meta.get("attempt_seq", 0) or 0),
                    checkpoint_ts=str(checkpoint_meta.get("checkpoint_ts", "") or now_iso),
                    intro_wrap_events=intro_wrap_events,
                )
                if not canonical_events:
                    consistency_flags.append("checkpoint_empty")
                missing_candidate_answer_questions = (
                    _find_questions_missing_candidate_answer_in_checkpoint(
                        checkpoint_meta["payload"],
                        selected_questions,
                    )
                )
                if missing_candidate_answer_questions:
                    if partial_mode:
                        consistency_flags.append("missing_candidate_answer")
                        consistency_flags.append("unanswered_questions_skipped")
                    else:
                        _fail_with_flag("missing_candidate_answer")
            else:
                consistency_flags.append("checkpoint_missing")
                if not partial_mode:
                    _fail_with_flag("checkpoint_missing")

            if partial_mode and not canonical_events:
                canonical_events = _build_canonical_events_from_turn_events(
                    conn,
                    token,
                    selected_questions=selected_questions,
                )
                if canonical_events:
                    canonical_source = CANONICAL_SOURCE_TURN_EVENTS_FALLBACK
                    consistency_flags.append("checkpoint_fallback_used")

            if not canonical_events:
                if partial_mode:
                    consistency_flags.append("canonical_turns_empty")
                else:
                    if checkpoint_meta:
                        _fail_with_flag("checkpoint_empty")
                    _fail_with_flag("canonical_turns_empty")

            if attempt_count > 1:
                consistency_flags.append("reconnect_merged")
            candidate_turn_count = sum(
                1 for event in canonical_events if str(event.get("role", "")) == "candidate"
            )
            if candidate_turn_count <= 0 and not partial_mode:
                _fail_with_flag("candidate_turns_empty")
            candidate_scorable_turn_count = 0
            for event in canonical_events:
                if str(event.get("role", "")) != "candidate":
                    continue
                question_id = str(event.get("question_id", "") or "").strip()
                try:
                    question_index = int(event.get("question_index", 0) or 0)
                except (TypeError, ValueError):
                    question_index = 0
                if question_id or question_index > 0:
                    candidate_scorable_turn_count += 1

            conn.execute(
                "DELETE FROM interview_turns WHERE interview_token = ?",
                (token,),
            )
            for idx, event in enumerate(canonical_events):
                conn.execute(
                    """
                    INSERT INTO interview_turns (
                        interview_token, role, content, created_at, sort_order
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        token,
                        str(event["role"]),
                        str(event["content"]),
                        str(event["created_at"] or now_iso),
                        idx,
                    ),
                )

            total_turn_events_row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM interview_turn_events
                WHERE interview_token = ?
                  AND role IN ('candidate', 'interviewer')
                  AND TRIM(text) != ''
                """,
                (token,),
            ).fetchone()
            total_turn_events = int(total_turn_events_row["c"] or 0) if total_turn_events_row else 0
            discarded_turn_count = max(0, total_turn_events - len(canonical_events))

            audio_rows = conn.execute(
                """
                SELECT s.track, s.file_path, s.duration_ms, s.segment_seq, a.attempt_seq
                FROM interview_audio_segments s
                JOIN interview_attempts a ON a.attempt_id = s.attempt_id
                WHERE s.interview_token = ?
                ORDER BY a.attempt_seq ASC, s.segment_seq ASC, s.id ASC
                """,
                (token,),
            ).fetchall()

            candidate_rows = [row for row in audio_rows if str(row["track"]) == "candidate"]
            interviewer_rows = [
                row for row in audio_rows if str(row["track"]) == "interviewer"
            ]
            candidate_path = _copy_or_merge_audio_track_rows(
                candidate_rows, token=token, track="candidate", flags=consistency_flags
            )
            interviewer_path = _copy_or_merge_audio_track_rows(
                interviewer_rows,
                token=token,
                track="interviewer",
                flags=consistency_flags,
            )

            score_inputs = build_score_inputs_from_canonical_turns(
                token,
                conn=conn,
                canonical_events=canonical_events,
            )
            filtered_score_inputs: List[Dict[str, Any]] = []
            for item in score_inputs:
                if not isinstance(item, dict):
                    continue
                candidate_answers = item.get("candidate_answers", [])
                if isinstance(candidate_answers, list) and any(
                    str(answer or "").strip() for answer in candidate_answers
                ):
                    filtered_score_inputs.append(item)
                    continue
                if str(item.get("aggregated_answer", "") or "").strip():
                    filtered_score_inputs.append(item)
            score_inputs = filtered_score_inputs
            mapped_candidate_turn_count = sum(
                len(item.get("candidate_answers", []) or [])
                for item in score_inputs
                if isinstance(item, dict)
            )
            if mapped_candidate_turn_count <= 0:
                if partial_mode:
                    consistency_flags.append("mapped_candidate_turns_empty")
                    consistency_flags.append("no_valid_answers_for_partial_scoring")
                else:
                    _fail_with_flag("mapped_candidate_turns_empty")
            if (
                candidate_scorable_turn_count > 0
                and mapped_candidate_turn_count < candidate_scorable_turn_count
            ):
                if partial_mode:
                    consistency_flags.append("score_turn_mismatch")
                else:
                    _fail_with_flag("score_turn_mismatch")
            if not candidate_path or not interviewer_path:
                if partial_mode:
                    consistency_flags.append("canonical_audio_missing")
                else:
                    _fail_with_flag("canonical_audio_missing")
            if not score_inputs:
                if partial_mode:
                    consistency_flags.append("score_input_empty")
                else:
                    _fail_with_flag("score_input_empty")
            if partial_mode:
                consistency_flags.append("partial_scoring")

            echo_risk_flags = _compute_echo_risk_flags(canonical_events)
            canonical_version_row = conn.execute(
                "SELECT COALESCE(canonical_version, 0) + 1 AS next_version FROM interviews WHERE token = ?",
                (token,),
            ).fetchone()
            next_version = int(canonical_version_row["next_version"] or 1)
            conn.execute(
                """
                UPDATE interviews
                SET candidate_audio_path = ?,
                    interviewer_audio_path = ?,
                    canonical_status = ?,
                    canonical_version = ?,
                    canonical_source = ?,
                    discarded_turn_count = ?,
                    echo_risk_flags = ?,
                    consistency_flags = ?,
                    updated_at = ?
                WHERE token = ?
                """,
                (
                    candidate_path,
                    interviewer_path,
                    INTERVIEW_CANONICAL_STATUS_READY,
                    next_version,
                    canonical_source,
                    discarded_turn_count,
                    ",".join(sorted(set(echo_risk_flags))),
                    ",".join(sorted(set(consistency_flags))),
                    now_iso,
                    token,
                ),
            )
            conn.commit()
        _invalidate_interview_cache(token)
        return {
            "ok": True,
            "attempt_count": attempt_count,
            "score_inputs": score_inputs,
            "canonical_source": canonical_source,
            "discarded_turn_count": discarded_turn_count,
            "echo_risk_flags": echo_risk_flags,
            "consistency_flags": sorted(set(consistency_flags)),
        }
    except Exception as finalize_error:
        merged_failed_flags = sorted(set(failed_consistency_flags))
        if not merged_failed_flags:
            merged_failed_flags = [f"finalize_failed:{type(finalize_error).__name__}"]
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE interviews
                SET canonical_status = ?, consistency_flags = ?, updated_at = ?
                WHERE token = ?
                """,
                (
                    INTERVIEW_CANONICAL_STATUS_FAILED,
                    ",".join(merged_failed_flags),
                    utc_now_iso(),
                    token,
                ),
            )
            conn.commit()
        _invalidate_interview_cache(token)
        return {"ok": False, "error": str(finalize_error)}


def clear_interview_turns(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM interview_turns WHERE interview_token = ?", (token,))
        conn.commit()


def save_interview_turns(token: str, turns: Sequence[Tuple[str, str, str]]) -> None:
    clear_interview_turns(token)
    with get_conn() as conn:
        for idx, (role, content, created_at) in enumerate(turns):
            conn.execute(
                """
                INSERT INTO interview_turns (interview_token, role, content, created_at, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, role, content, created_at, idx),
            )
        conn.commit()


def init_interview_scorecard(token: str) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_scorecards (
                interview_token, status, overall_score, total_score, total_max_score,
                error_message, started_at, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interview_token) DO UPDATE SET
                status = excluded.status,
                overall_score = excluded.overall_score,
                total_score = excluded.total_score,
                total_max_score = excluded.total_max_score,
                error_message = excluded.error_message,
                started_at = excluded.started_at,
                completed_at = excluded.completed_at,
                updated_at = excluded.updated_at
            """,
            (
                token,
                SCORECARD_STATUS_PENDING,
                None,
                None,
                None,
                None,
                now,
                None,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM interview_question_scores WHERE interview_token = ?",
            (token,),
        )
        conn.commit()


def save_interview_scorecard_success(
    token: str,
    total_score: float,
    total_max_score: float,
    question_scores: Sequence[Dict[str, Any]],
) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_scorecards (
                interview_token, status, overall_score, total_score, total_max_score,
                error_message, started_at, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interview_token) DO UPDATE SET
                status = excluded.status,
                overall_score = excluded.overall_score,
                total_score = excluded.total_score,
                total_max_score = excluded.total_max_score,
                error_message = excluded.error_message,
                completed_at = excluded.completed_at,
                updated_at = excluded.updated_at
            """,
            (
                token,
                SCORECARD_STATUS_COMPLETED,
                None,
                float(total_score),
                float(total_max_score),
                None,
                now,
                now,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM interview_question_scores WHERE interview_token = ?",
            (token,),
        )
        for item in question_scores:
            conn.execute(
                """
                INSERT INTO interview_question_scores (
                    interview_token, question_id, sort_order, question, ability_dimension,
                    output_format, score_format, comment_requirement,
                    aggregated_answer, max_score, score_error, numeric_score, comment
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    str(item.get("question_id", "") or ""),
                    int(item.get("sort_order", 0) or 0),
                    str(item.get("question", "") or ""),
                    str(item.get("ability_dimension", "") or ""),
                    "",
                    str(item.get("score_format", "") or ""),
                    str(item.get("comment_requirement", "") or ""),
                    str(item.get("aggregated_answer", "") or ""),
                    float(item.get("max_score", 0.0) or 0.0),
                    str(item.get("score_error", "") or ""),
                    float(item.get("numeric_score", 0.0) or 0.0),
                    str(item.get("comment", "") or ""),
                ),
            )
        conn.commit()


def save_interview_scorecard_failed(token: str, error_message: str) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_scorecards (
                interview_token, status, overall_score, total_score, total_max_score,
                error_message, started_at, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interview_token) DO UPDATE SET
                status = excluded.status,
                overall_score = excluded.overall_score,
                total_score = excluded.total_score,
                total_max_score = excluded.total_max_score,
                error_message = excluded.error_message,
                completed_at = excluded.completed_at,
                updated_at = excluded.updated_at
            """,
            (
                token,
                SCORECARD_STATUS_FAILED,
                None,
                None,
                None,
                (error_message or "").strip()[:2000],
                now,
                now,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM interview_question_scores WHERE interview_token = ?",
            (token,),
        )
        conn.commit()


def _load_interview_scorecard_in_conn(
    conn: sqlite3.Connection, token: str
) -> Optional[Dict[str, Any]]:
    card = conn.execute(
        """
        SELECT interview_token, status, overall_score, total_score, total_max_score,
               error_message, started_at, completed_at
        FROM interview_scorecards
        WHERE interview_token = ?
        """,
        (token,),
    ).fetchone()
    if not card:
        return None

    rows = conn.execute(
        """
        SELECT question_id, sort_order, question, ability_dimension, output_format,
               score_format, comment_requirement,
               aggregated_answer, max_score, score_error, numeric_score, comment
        FROM interview_question_scores
        WHERE interview_token = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (token,),
    ).fetchall()
    return {
        "status": card["status"],
        "overall_score": (
            None if card["overall_score"] is None else float(card["overall_score"])
        ),
        "total_score": (
            None if card["total_score"] is None else float(card["total_score"])
        ),
        "total_max_score": (
            None if card["total_max_score"] is None else float(card["total_max_score"])
        ),
        "started_at": card["started_at"],
        "completed_at": card["completed_at"],
        "error_message": card["error_message"],
        "question_scores": [
            {
                "question_id": row["question_id"],
                "sort_order": int(row["sort_order"] or 0),
                "question": row["question"],
                "ability_dimension": row["ability_dimension"],
                "output_format": row["output_format"],
                "score_format": row["score_format"],
                "comment_requirement": row["comment_requirement"],
                "aggregated_answer": row["aggregated_answer"],
                "max_score": float(row["max_score"] or 0.0),
                "score_error": row["score_error"],
                "numeric_score": float(row["numeric_score"] or 0.0),
                "comment": row["comment"],
            }
            for row in rows
        ],
    }


def _write_pcm_to_wav(path: Path, pcm_bytes: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)


def _is_mp3_audio(data: bytes) -> bool:
    if not data:
        return False
    if data.startswith(b"ID3"):
        return True
    scan_limit = min(len(data) - 1, 4096)
    for idx in range(scan_limit):
        if data[idx] == 0xFF and (data[idx + 1] & 0xE0) == 0xE0:
            return True
    return False


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _run_ffmpeg(args: List[str], input_bytes: bytes) -> Optional[bytes]:
    ffmpeg_bin = (os.getenv("FFMPEG_BIN") or "ffmpeg").strip() or "ffmpeg"
    if shutil.which(ffmpeg_bin) is None:
        INFO(f"[InterviewPersist] ffmpeg unavailable bin={ffmpeg_bin}")
        return None
    try:
        completed = subprocess.run(
            [ffmpeg_bin, *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception as ffmpeg_err:
        INFO(f"[InterviewPersist] ffmpeg execution failed error={ffmpeg_err}")
        return None
    if completed.returncode != 0 or not completed.stdout:
        stderr_preview = completed.stderr.decode("utf-8", errors="replace")[:400]
        INFO(
            "[InterviewPersist] ffmpeg encode failed "
            f"code={completed.returncode} stderr={stderr_preview}"
        )
        return None
    return bytes(completed.stdout)


def _ffmpeg_encode_pcm_to_mp3(
    pcm_bytes: bytes, *, sample_rate: int, channels: int, bitrate_kbps: int
) -> Optional[bytes]:
    if not pcm_bytes:
        return None
    return _run_ffmpeg(
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
            "-acodec",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            "-f",
            "mp3",
            "pipe:1",
        ],
        pcm_bytes,
    )


def _ffmpeg_reencode_mp3(
    mp3_bytes: bytes, *, sample_rate: int, channels: int, bitrate_kbps: int
) -> Optional[bytes]:
    if not mp3_bytes:
        return None
    return _run_ffmpeg(
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-acodec",
            "libmp3lame",
            "-b:a",
            f"{bitrate_kbps}k",
            "-f",
            "mp3",
            "pipe:1",
        ],
        mp3_bytes,
    )


def _probe_duration_ms(path: Path) -> int:
    ffprobe_bin = (os.getenv("FFPROBE_BIN") or "ffprobe").strip() or "ffprobe"
    if shutil.which(ffprobe_bin) is None:
        return 0
    try:
        completed = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except Exception:
        return 0
    if completed.returncode != 0:
        return 0
    try:
        seconds = float((completed.stdout or "").strip() or "0")
    except ValueError:
        return 0
    if seconds <= 0:
        return 0
    return max(1, int(round(seconds * 1000)))


def persist_interview_audio(
    token: str,
    candidate_pcm_bytes: bytes,
    interviewer_encoded_bytes: bytes,
    *,
    attempt_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    normalized_attempt_id = str(attempt_id or "").strip()
    interview_dir = AUDIO_DIR / token
    candidate_segment_seq = 1
    interviewer_segment_seq = 1
    if normalized_attempt_id:
        interview_dir = interview_dir / normalized_attempt_id
        with get_conn() as conn:
            candidate_segment_seq_row = conn.execute(
                """
                SELECT COALESCE(MAX(segment_seq), 0) + 1 AS next_seq
                FROM interview_audio_segments
                WHERE attempt_id = ? AND track = 'candidate'
                """,
                (normalized_attempt_id,),
            ).fetchone()
            interviewer_segment_seq_row = conn.execute(
                """
                SELECT COALESCE(MAX(segment_seq), 0) + 1 AS next_seq
                FROM interview_audio_segments
                WHERE attempt_id = ? AND track = 'interviewer'
                """,
                (normalized_attempt_id,),
            ).fetchone()
            candidate_segment_seq = int(candidate_segment_seq_row["next_seq"] or 1)
            interviewer_segment_seq = int(interviewer_segment_seq_row["next_seq"] or 1)
    interview_dir.mkdir(parents=True, exist_ok=True)

    candidate_path: Optional[Path] = None
    interviewer_path: Optional[Path] = None
    candidate_duration_ms = 0
    interviewer_duration_ms = 0
    compress_enabled = _env_flag("AUDIO_COMPRESS_ENABLED", True)
    candidate_bitrate_kbps = _env_int(
        "CANDIDATE_MP3_BITRATE_KBPS", 32, minimum=8, maximum=320
    )
    interviewer_bitrate_kbps = _env_int(
        "INTERVIEWER_MP3_BITRATE_KBPS", 32, minimum=8, maximum=320
    )
    candidate_sample_rate = _env_int(
        "CANDIDATE_AUDIO_SAMPLE_RATE", 16000, minimum=8000, maximum=48000
    )
    interviewer_sample_rate = _env_int(
        "INTERVIEWER_AUDIO_SAMPLE_RATE", 24000, minimum=8000, maximum=48000
    )
    candidate_channels = _env_int("CANDIDATE_AUDIO_CHANNELS", 1, minimum=1, maximum=2)
    interviewer_channels = _env_int(
        "INTERVIEWER_AUDIO_CHANNELS", 1, minimum=1, maximum=2
    )

    if candidate_pcm_bytes:
        candidate_mp3_bytes: Optional[bytes] = None
        if compress_enabled:
            candidate_mp3_bytes = _ffmpeg_encode_pcm_to_mp3(
                candidate_pcm_bytes,
                sample_rate=candidate_sample_rate,
                channels=candidate_channels,
                bitrate_kbps=candidate_bitrate_kbps,
            )
        if candidate_mp3_bytes:
            if normalized_attempt_id:
                candidate_path = interview_dir / f"candidate-{candidate_segment_seq}.mp3"
            else:
                candidate_path = interview_dir / "candidate.mp3"
            candidate_path.write_bytes(candidate_mp3_bytes)
            candidate_duration_ms = _probe_duration_ms(candidate_path)
            INFO(
                f"[InterviewPersist] token={token} candidate_format=mp3 "
                f"candidate_bytes={len(candidate_mp3_bytes)}"
            )
        else:
            if normalized_attempt_id:
                candidate_path = interview_dir / f"candidate-{candidate_segment_seq}.wav"
            else:
                candidate_path = interview_dir / "candidate.wav"
            _write_pcm_to_wav(
                candidate_path, candidate_pcm_bytes, sample_rate=candidate_sample_rate
            )
            denominator = max(1, candidate_sample_rate * candidate_channels * 2)
            candidate_duration_ms = max(1, int(round(len(candidate_pcm_bytes) * 1000 / denominator)))
            INFO(
                f"[InterviewPersist] token={token} candidate_format=wav "
                f"candidate_bytes={len(candidate_pcm_bytes)}"
            )

    if interviewer_encoded_bytes:
        interviewer_bytes_to_write = interviewer_encoded_bytes
        extension = "mp3" if _is_mp3_audio(interviewer_encoded_bytes) else "raw"
        if extension == "mp3" and compress_enabled:
            reencoded_bytes = _ffmpeg_reencode_mp3(
                interviewer_encoded_bytes,
                sample_rate=interviewer_sample_rate,
                channels=interviewer_channels,
                bitrate_kbps=interviewer_bitrate_kbps,
            )
            if reencoded_bytes:
                interviewer_bytes_to_write = reencoded_bytes
        if normalized_attempt_id:
            interviewer_path = interview_dir / f"interviewer-{interviewer_segment_seq}.{extension}"
        else:
            interviewer_path = interview_dir / f"interviewer.{extension}"
        interviewer_path.write_bytes(interviewer_bytes_to_write)
        if extension == "raw":
            denominator = max(1, interviewer_sample_rate * interviewer_channels * 2)
            interviewer_duration_ms = max(
                1, int(round(len(interviewer_bytes_to_write) * 1000 / denominator))
            )
        else:
            interviewer_duration_ms = _probe_duration_ms(interviewer_path)
        INFO(
            f"[InterviewPersist] token={token} interviewer_format={extension} "
            f"interviewer_bytes={len(interviewer_bytes_to_write)}"
        )

    with get_conn() as conn:
        if normalized_attempt_id:
            now_iso = utc_now_iso()
            if candidate_path:
                conn.execute(
                    """
                    INSERT INTO interview_audio_segments (
                        attempt_id, interview_token, track, segment_seq, file_path,
                        duration_ms, started_at, ended_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_attempt_id,
                        token,
                        "candidate",
                        candidate_segment_seq,
                        str(candidate_path),
                        int(candidate_duration_ms),
                        now_iso,
                        now_iso,
                    ),
                )
            if interviewer_path:
                conn.execute(
                    """
                    INSERT INTO interview_audio_segments (
                        attempt_id, interview_token, track, segment_seq, file_path,
                        duration_ms, started_at, ended_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_attempt_id,
                        token,
                        "interviewer",
                        interviewer_segment_seq,
                        str(interviewer_path),
                        int(interviewer_duration_ms),
                        now_iso,
                        now_iso,
                    ),
                )
        else:
            conn.execute(
                """
                UPDATE interviews
                SET candidate_audio_path = ?, interviewer_audio_path = ?, updated_at = ?
                WHERE token = ?
                """,
                (
                    str(candidate_path) if candidate_path else None,
                    str(interviewer_path) if interviewer_path else None,
                    utc_now_iso(),
                    token,
                ),
            )
        conn.commit()

    return {
        "candidate_audio_path": str(candidate_path) if candidate_path else None,
        "interviewer_audio_path": str(interviewer_path) if interviewer_path else None,
    }


def mark_interview_completed(token: str, completed_reason: Optional[str] = None) -> None:
    if completed_reason is not None and completed_reason not in INTERVIEW_COMPLETED_REASONS:
        raise ValueError("invalid_completed_reason")
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE interviews
            SET status = ?, completed_at = ?, completed_reason = ?, reconnect_deadline_at = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_STATUS_COMPLETED, now, completed_reason, None, now, token),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    INTERVIEW_EXPIRY_INDEX.remove(token)


def mark_interview_failed(token: str) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return
        current_status = str(row["status"] or "")
        if current_status == INTERVIEW_STATUS_COMPLETED:
            return
        conn.execute(
            """
            UPDATE interviews
            SET status = ?, reconnect_deadline_at = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_STATUS_FAILED, None, now, token),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    INTERVIEW_EXPIRY_INDEX.remove(token)


def mark_interview_disconnected(token: str, grace_seconds: int = 30) -> bool:
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(seconds=max(1, grace_seconds))).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        if not _is_interview_active(row["status"]):
            return False
        conn.execute(
            """
            UPDATE interviews
            SET status = ?, reconnect_deadline_at = ?, updated_at = ?
            WHERE token = ?
            """,
            (INTERVIEW_STATUS_IN_PROGRESS, deadline, now.isoformat(), token),
        )
        conn.commit()
    _invalidate_interview_cache(token)
    return True


def update_interview_updated_at(token: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE interviews SET updated_at = ? WHERE token = ?",
            (utc_now_iso(), token),
        )
        conn.commit()


def get_audio_file_path(token: str, track: str) -> Optional[Path]:
    if track not in ("candidate", "interviewer"):
        return None

    with get_conn() as conn:
        row = conn.execute(
            "SELECT candidate_audio_path, interviewer_audio_path FROM interviews WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None

    path_str = row["candidate_audio_path"] if track == "candidate" else row["interviewer_audio_path"]
    if not path_str:
        return None
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return None
    return path


def get_interview_log_file_path(token: str, stream: str) -> Optional[Path]:
    if stream not in ("backend", "frontend"):
        return None

    with get_conn() as conn:
        row = conn.execute("SELECT token FROM interviews WHERE token = ?", (token,)).fetchone()
        if not row:
            return None

    path = INTERVIEW_LOG_DIR / token / f"{stream}.log"
    if not path.exists() or not path.is_file():
        return None
    return path
