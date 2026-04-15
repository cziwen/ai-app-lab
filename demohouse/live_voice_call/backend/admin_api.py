import asyncio
import csv
import hashlib
import io
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool
from score_scale import parse_score_scale
from stt_audio_url import verify_audio_access_signature

from admin_login_guard import AdminLoginGuard, load_admin_login_guard_config
from admin_store import (
    ADMIN_SESSION_COOKIE,
    create_admin_session,
    create_interview,
    create_job,
    delete_interview,
    delete_job_cascade,
    ensure_default_admin,
    get_admin_by_session,
    get_audio_file_path,
    get_interview_detail,
    get_interview_log_file_path,
    get_job_detail,
    get_public_access,
    list_interviews,
    list_jobs,
    revoke_admin_session,
    update_job,
    verify_admin_credentials,
)

CSV_TEMPLATE_COLUMNS = [
    "场景",
    "问题",
    "评分标准",
    "最大分数",
]
ADMIN_AUTH_LOGGER = logging.getLogger("admin.auth")


def _load_cors_origins() -> List[str]:
    raw = os.getenv("ADMIN_CORS_ORIGINS")
    if raw and raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ]


def build_interview_link(token: str) -> str:
    raw = (os.getenv("INTERVIEW_BASE_DOMAIN") or "").strip()
    if not raw:
        raise HTTPException(status_code=500, detail="INTERVIEW_BASE_DOMAIN missing")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    domain = (parsed.netloc or "").strip()
    if not domain:
        raise HTTPException(status_code=500, detail="INTERVIEW_BASE_DOMAIN invalid")
    normalized_base = f"{scheme}://{domain}".rstrip("/")
    return f"{normalized_base}/check-in?token={token}"


def parse_question_csv(upload: UploadFile) -> List[Dict[str, str]]:
    content = upload.file.read()
    upload.file.close()
    if not content:
        raise HTTPException(status_code=400, detail="CSV 文件为空")

    decoded: Optional[str] = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise HTTPException(status_code=400, detail="CSV 编码无法识别，请使用 UTF-8")

    reader = csv.reader(io.StringIO(decoded))

    try:
        header = next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="CSV 缺少表头")
    normalized_header = [str(cell).strip() for cell in header]
    if normalized_header != CSV_TEMPLATE_COLUMNS:
        expected = ",".join(CSV_TEMPLATE_COLUMNS)
        actual = ",".join(normalized_header) if normalized_header else "(空)"
        raise HTTPException(
            status_code=400,
            detail=f"CSV 表头不匹配。期望: {expected}；实际: {actual}",
        )

    rows: List[Dict[str, str]] = []
    current_scene = ""
    current_scene_index = 0
    scene_requires_rubric = False
    for row_index, row in enumerate(reader, start=2):
        if not row or all(not str(cell).strip() for cell in row):
            continue

        normalized_row = [str(cell).strip() for cell in row]
        if len(normalized_row) < len(CSV_TEMPLATE_COLUMNS):
            normalized_row.extend([""] * (len(CSV_TEMPLATE_COLUMNS) - len(normalized_row)))
        elif len(normalized_row) > len(CSV_TEMPLATE_COLUMNS):
            normalized_row = normalized_row[: len(CSV_TEMPLATE_COLUMNS)]

        scenario = normalized_row[0]
        question = normalized_row[1]
        scoring_boundary = normalized_row[2]
        score_format = normalized_row[3]

        if not question:
            raise HTTPException(status_code=400, detail=f"CSV 第{row_index}行“问题”不能为空")

        is_new_scene = bool(scenario)
        if is_new_scene:
            current_scene = scenario
            current_scene_index += 1
            scene_requires_rubric = True
        elif not current_scene:
            raise HTTPException(
                status_code=400,
                detail=f"CSV 第{row_index}行“场景”为空，且前面没有可延续的场景",
            )

        if scene_requires_rubric:
            if not scoring_boundary:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV 第{row_index}行“评分标准”不能为空（场景首问必填）",
                )
            if not score_format:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV 第{row_index}行“最大分数”不能为空（场景首问必填）",
                )
            max_score, scale_error = parse_score_scale(score_format)
            if max_score is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"CSV 第{row_index}行“最大分数”格式无效：{score_format or '(空)'}；"
                        f"{scale_error or '请使用 5/5分/0-5/0~5/0～5分'}"
                    ),
                )
            scene_requires_rubric = False
        else:
            if scoring_boundary:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV 第{row_index}行“评分标准”必须留空（场景子问不允许填写）",
                )
            if score_format:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV 第{row_index}行“最大分数”必须留空（场景子问不允许填写）",
                )

        rows.append(
            {
                "scenario": current_scene,
                "scene_segment": f"scene-{current_scene_index}",
                "question": question,
                "scoring_boundary": scoring_boundary,
                "score_format": score_format,
            }
        )

    if not rows:
        raise HTTPException(status_code=400, detail="CSV 题库为空")

    return rows


def _session_cookie_params() -> Dict[str, Any]:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": False,
        "path": "/",
        "max_age": 12 * 3600,
    }


def _extract_client_ip(request: Request) -> str:
    x_forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if x_forwarded_for:
        first = x_forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "-"


def _resolve_request_id(request: Request) -> str:
    incoming = (request.headers.get("x-request-id") or "").strip()
    if incoming:
        return incoming
    return uuid.uuid4().hex


def _hash_username(username: str) -> str:
    normalized = (username or "").strip().lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:16]


def _get_stt_audio_signing_secret() -> str:
    return str(os.getenv("STT_AUDIO_SIGNING_SECRET") or "").strip()


def _log_login_event(
    *,
    outcome: str,
    ip: str,
    username_hash: str,
    fail_count_ip: int,
    fail_count_userip: int,
    lock_seconds: int,
    retry_after: int,
    latency_ms: int,
    request_id: str,
) -> None:
    ADMIN_AUTH_LOGGER.info(
        "event=admin_auth_login outcome=%s ip=%s username_hash=%s fail_count_ip=%s fail_count_userip=%s lock_seconds=%s retry_after=%s latency_ms=%s request_id=%s",
        outcome,
        ip,
        username_hash,
        fail_count_ip,
        fail_count_userip,
        lock_seconds,
        retry_after,
        latency_ms,
        request_id,
    )


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class QuestionFollowupBody(BaseModel):
    question_id: int = Field(ge=1)
    max_followups: int = Field(ge=0, le=3)
    max_clarifies: int = Field(ge=0, le=3)


class CreateInterviewBody(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    job_uid: str = Field(min_length=1)
    question_followups: List[QuestionFollowupBody] = Field(min_items=1)
    notes: Optional[str] = Field(default=None, max_length=1000)
    required_checkins: Optional[List[str]] = Field(default=None)
    enable_live_subtitle: Optional[StrictBool] = Field(default=False)


def create_admin_app(
    active_interview_metrics_provider: Optional[Callable[[], Dict[str, int]]] = None,
    login_guard: Optional[AdminLoginGuard] = None,
) -> FastAPI:
    ensure_default_admin()
    auth_guard = login_guard or AdminLoginGuard(load_admin_login_guard_config())

    app = FastAPI(title="AI Interview Admin API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_load_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_admin(request: Request) -> Dict[str, Any]:
        token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
        admin = get_admin_by_session(token)
        if not admin:
            raise HTTPException(status_code=401, detail="未登录或登录已过期")
        return admin

    @app.get("/api/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/admin/auth/login")
    async def login(body: LoginBody, request: Request, response: Response) -> Dict[str, Any]:
        started = time.monotonic()
        username = body.username.strip()
        ip = _extract_client_ip(request)
        request_id = _resolve_request_id(request)
        username_hash = _hash_username(username)

        fail_count_ip, fail_count_userip = auth_guard.get_failure_counts(username, ip)
        locked, retry_after = auth_guard.is_locked(username, ip)
        if locked:
            latency_ms = int((time.monotonic() - started) * 1000)
            _log_login_event(
                outcome="locked",
                ip=ip,
                username_hash=username_hash,
                fail_count_ip=fail_count_ip,
                fail_count_userip=fail_count_userip,
                lock_seconds=retry_after,
                retry_after=retry_after,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后重试",
                headers={"Retry-After": str(max(1, retry_after))},
            )

        admin_id = verify_admin_credentials(username, body.password)
        if not admin_id:
            fail_count_ip, fail_count_userip, lock_seconds = auth_guard.record_failure(
                username, ip
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            _log_login_event(
                outcome="invalid_credentials",
                ip=ip,
                username_hash=username_hash,
                fail_count_ip=fail_count_ip,
                fail_count_userip=fail_count_userip,
                lock_seconds=lock_seconds,
                retry_after=0,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        auth_guard.clear_success(username, ip)
        session_token = create_admin_session(admin_id)
        response.set_cookie(ADMIN_SESSION_COOKIE, session_token, **_session_cookie_params())
        latency_ms = int((time.monotonic() - started) * 1000)
        _log_login_event(
            outcome="success",
            ip=ip,
            username_hash=username_hash,
            fail_count_ip=0,
            fail_count_userip=0,
            lock_seconds=0,
            retry_after=0,
            latency_ms=latency_ms,
            request_id=request_id,
        )
        return {"ok": True}

    @app.post("/api/admin/auth/logout")
    async def logout(request: Request, response: Response) -> Dict[str, Any]:
        token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
        if token:
            revoke_admin_session(token)
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/admin/auth/me")
    async def me(admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        return {"admin": {"id": admin["admin_id"], "username": admin["username"]}}

    @app.get("/api/admin/jobs")
    async def get_jobs(
        q: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> Dict[str, Any]:
        return list_jobs(q, page, page_size)

    @app.post("/api/admin/jobs")
    async def post_job(
        name: str = Form(...),
        duties: str = Form(...),
        requirements: str = Form(...),
        notes: Optional[str] = Form(default=None),
        question_bank: UploadFile = File(...),
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> Dict[str, Any]:
        rows = parse_question_csv(question_bank)
        result = create_job(
            name=name.strip(),
            duties=duties.strip(),
            requirements=requirements.strip(),
            notes=(notes or "").strip() or None,
            csv_filename=question_bank.filename,
            questions=rows,
        )
        return {"job": result}

    @app.get("/api/admin/jobs/{job_uid}")
    async def get_job(job_uid: str, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        detail = get_job_detail(job_uid)
        if not detail:
            raise HTTPException(status_code=404, detail="岗位不存在")
        return {"job": detail}

    @app.put("/api/admin/jobs/{job_uid}")
    async def put_job(
        job_uid: str,
        name: str = Form(...),
        duties: str = Form(...),
        requirements: str = Form(...),
        notes: Optional[str] = Form(default=None),
        expected_updated_at: str = Form(...),
        question_bank: Optional[UploadFile] = File(default=None),
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> Dict[str, Any]:
        rows = parse_question_csv(question_bank) if question_bank else None
        try:
            job = update_job(
                job_uid=job_uid,
                name=name.strip(),
                duties=duties.strip(),
                requirements=requirements.strip(),
                notes=(notes or "").strip() or None,
                expected_updated_at=expected_updated_at.strip(),
                csv_filename=question_bank.filename if question_bank else None,
                questions=rows,
            )
        except ValueError as e:
            if str(e) == "job_not_found":
                raise HTTPException(status_code=404, detail="岗位不存在")
            if str(e) == "job_conflict":
                raise HTTPException(status_code=409, detail="岗位已被他人更新，请刷新后重试")
            if str(e) == "invalid_expected_updated_at":
                raise HTTPException(status_code=400, detail="expected_updated_at 不能为空")
            raise
        return {"job": job}

    @app.delete("/api/admin/jobs/{job_uid}")
    async def remove_job(job_uid: str, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        try:
            ok = delete_job_cascade(job_uid)
        except ValueError as e:
            if str(e) == "job_has_interviews":
                raise HTTPException(status_code=409, detail="该岗位已有面试记录，不能删除")
            raise
        if not ok:
            raise HTTPException(status_code=404, detail="岗位不存在")
        return {"ok": True}

    @app.get("/api/admin/interviews")
    async def get_interview_list(
        q: str = Query(default=""),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> Dict[str, Any]:
        return list_interviews(q, page, page_size)

    @app.get("/api/admin/interviews/metrics")
    async def get_interview_metrics(
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> Dict[str, int]:
        if active_interview_metrics_provider is None:
            raise HTTPException(status_code=503, detail="实时指标服务不可用")
        try:
            metrics = await asyncio.to_thread(active_interview_metrics_provider)
        except Exception as provider_err:
            raise HTTPException(status_code=503, detail="实时指标服务不可用") from provider_err
        active_interviews = metrics.get("active_interviews")
        max_active_interviews = metrics.get("max_active_interviews")
        if not isinstance(active_interviews, int) or not isinstance(max_active_interviews, int):
            raise HTTPException(status_code=503, detail="实时指标服务不可用")
        return {
            "active_interviews": active_interviews,
            "max_active_interviews": max_active_interviews,
        }

    @app.post("/api/admin/interviews")
    async def post_interview(body: CreateInterviewBody, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        try:
            interview = create_interview(
                candidate_name=body.candidate_name.strip(),
                job_uid=body.job_uid.strip(),
                notes=(body.notes or "").strip() or None,
                question_followups=[item.model_dump() for item in body.question_followups],
                required_checkins=body.required_checkins,
                enable_live_subtitle=bool(body.enable_live_subtitle),
            )
        except ValueError as e:
            if str(e) == "job_not_found":
                raise HTTPException(status_code=404, detail="岗位不存在")
            if str(e) == "job_question_bank_empty":
                raise HTTPException(status_code=400, detail="岗位题库为空")
            if str(e) == "invalid_required_checkins":
                raise HTTPException(
                    status_code=400,
                    detail="required_checkins 仅支持 speaker/mic/camera/screen",
                )
            if str(e) == "invalid_question_followups":
                raise HTTPException(
                    status_code=400,
                    detail="question_followups 必须完整覆盖岗位题目，且 max_followups/max_clarifies 取值范围为 0-3",
                )
            raise
        interview["interview_link"] = build_interview_link(interview["token"])
        return {"interview": interview}

    @app.get("/api/admin/interviews/{token}")
    async def get_interview(token: str, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        detail = get_interview_detail(token)
        if not detail:
            raise HTTPException(status_code=404, detail="面试不存在")

        completed = detail["status"] == "completed"
        raw_scorecard = detail.get("scorecard") if isinstance(detail, dict) else None
        scorecard = (
            raw_scorecard
            if isinstance(raw_scorecard, dict)
            else {
                "status": "pending",
                "overall_score": None,
                "total_score": None,
                "total_max_score": None,
                "completed_at": None,
                "error_message": None,
                "question_scores": [],
            }
        )
        response: Dict[str, Any] = {
            "interview": {
                "token": detail["token"],
                "candidate_name": detail["candidate_name"],
                "question_count": detail["question_count"],
                "notes": detail["notes"],
                "status": detail["status"],
                "interruption_count": detail.get("interruption_count", 0),
                "attempt_count": detail.get("attempt_count", 0),
                "canonical_status": detail.get("canonical_status", "ready"),
                "canonical_version": detail.get("canonical_version", 0),
                "canonical_source": detail.get("canonical_source", "turn_events_fallback"),
                "discarded_turn_count": detail.get("discarded_turn_count", 0),
                "echo_risk_flags": detail.get("echo_risk_flags", []),
                "consistency_flags": detail.get("consistency_flags", []),
                "created_at": detail["created_at"],
                "completed_at": detail["completed_at"],
                "completed_reason": detail.get("completed_reason"),
                "job": detail["job"],
                "selected_questions": detail.get("selected_questions", []),
                "required_checkins": detail.get("required_checkins", []),
                "enable_live_subtitle": bool(detail.get("enable_live_subtitle", False)),
                "interview_link": build_interview_link(detail["token"]),
                "completed": completed,
                "scorecard": {
                    "status": str(scorecard.get("status", "pending") or "pending"),
                    "overall_score": scorecard.get("overall_score"),
                    "total_score": scorecard.get("total_score"),
                    "total_max_score": scorecard.get("total_max_score"),
                    "completed_at": scorecard.get("completed_at"),
                    "error_message": scorecard.get("error_message"),
                    "question_scores": scorecard.get("question_scores", []),
                },
            }
        }

        if completed:
            response["interview"]["turns"] = detail["turns"]
            response["interview"]["audio"] = {
                "candidate_url": f"/api/admin/interviews/{token}/audio/candidate",
                "interviewer_url": f"/api/admin/interviews/{token}/audio/interviewer",
            }
        else:
            response["interview"]["completion_message"] = "用户还没有完成面试"

        return response

    @app.delete("/api/admin/interviews/{token}")
    async def remove_interview(token: str, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        ok = delete_interview(token)
        if not ok:
            raise HTTPException(status_code=404, detail="面试不存在")
        return {"ok": True}

    @app.get("/api/admin/interviews/{token}/audio/{track}")
    async def get_audio(token: str, track: str, _admin: Dict[str, Any] = Depends(require_admin)) -> FileResponse:
        path = get_audio_file_path(token, track)
        if not path:
            raise HTTPException(status_code=404, detail="音频不存在")
        suffix = path.suffix.lower()
        if suffix == ".wav":
            media_type = "audio/wav"
        elif suffix == ".mp3":
            media_type = "audio/mpeg"
        else:
            media_type = "application/octet-stream"
        return FileResponse(path=path, media_type=media_type, filename=Path(path).name)

    @app.get("/api/admin/interviews/{token}/logs/{stream}")
    async def get_interview_log(
        token: str,
        stream: str,
        tail_lines: int = Query(default=2000, ge=1, le=20000),
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> Dict[str, Any]:
        path = get_interview_log_file_path(token, stream)
        if not path:
            raise HTTPException(status_code=404, detail="日志不存在")

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        truncated = len(lines) > tail_lines
        selected_lines = lines[-tail_lines:] if truncated else lines
        content = "\n".join(selected_lines)
        if selected_lines:
            content = f"{content}\n"

        return {
            "token": token,
            "stream": stream,
            "path": str(path),
            "line_count": len(lines),
            "returned_line_count": len(selected_lines),
            "truncated": truncated,
            "content": content,
        }

    @app.get("/api/admin/interviews/{token}/logs/{stream}/download")
    async def download_interview_log(
        token: str,
        stream: str,
        _admin: Dict[str, Any] = Depends(require_admin),
    ) -> FileResponse:
        path = get_interview_log_file_path(token, stream)
        if not path:
            raise HTTPException(status_code=404, detail="日志不存在")
        return FileResponse(path=path, media_type="text/plain; charset=utf-8", filename=Path(path).name)

    @app.get("/api/public/interviews/{token}/access")
    async def public_interview_access(token: str) -> Dict[str, Any]:
        detail = get_public_access(token)
        if not detail:
            raise HTTPException(status_code=404, detail="面试链接无效或已失效")
        return {"interview": detail}

    @app.get("/api/public/interviews/{token}/audio/{track}")
    async def public_interview_audio(
        token: str,
        track: str,
        exp: int = Query(..., description="unix timestamp in seconds"),
        sig: str = Query(..., min_length=1),
        question_id: Optional[str] = Query(default=None),
        question_epoch: int = Query(default=0, ge=0),
    ) -> FileResponse:
        secret = _get_stt_audio_signing_secret()
        if not secret:
            raise HTTPException(status_code=503, detail="音频服务暂不可用")
        try:
            is_valid = verify_audio_access_signature(
                token,
                track,
                exp,
                sig,
                secret=secret,
            )
        except ValueError:
            is_valid = False
        if not is_valid:
            raise HTTPException(status_code=403, detail="音频签名无效或已过期")
        path = get_audio_file_path(
            token,
            track,
            question_id=str(question_id or "").strip() or None,
            question_epoch=question_epoch,
        )
        if not path:
            raise HTTPException(status_code=404, detail="音频不存在")
        suffix = path.suffix.lower()
        if suffix == ".wav":
            media_type = "audio/wav"
        elif suffix == ".mp3":
            media_type = "audio/mpeg"
        else:
            media_type = "application/octet-stream"
        return FileResponse(path=path, media_type=media_type, filename=Path(path).name)

    return app
