import asyncio
import csv
import io
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from score_scale import parse_score_scale

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
    get_job_detail,
    get_public_access,
    list_interviews,
    list_jobs,
    revoke_admin_session,
    verify_admin_credentials,
)

CSV_TEMPLATE_COLUMNS = [
    "场景",
    "问题",
    "评分标准",
    "最大分数",
]


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


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class QuestionFollowupBody(BaseModel):
    question_id: int = Field(ge=1)
    max_followups: int = Field(ge=0, le=3)


class CreateInterviewBody(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    job_uid: str = Field(min_length=1)
    question_followups: List[QuestionFollowupBody] = Field(min_items=1)
    notes: Optional[str] = Field(default=None, max_length=1000)
    required_checkins: Optional[List[str]] = Field(default=None)


def create_admin_app(
    active_interview_metrics_provider: Optional[Callable[[], Dict[str, int]]] = None,
) -> FastAPI:
    ensure_default_admin()

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
    async def login(body: LoginBody, response: Response) -> Dict[str, Any]:
        admin_id = verify_admin_credentials(body.username.strip(), body.password)
        if not admin_id:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        session_token = create_admin_session(admin_id)
        response.set_cookie(ADMIN_SESSION_COOKIE, session_token, **_session_cookie_params())
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

    @app.delete("/api/admin/jobs/{job_uid}")
    async def remove_job(job_uid: str, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        ok = delete_job_cascade(job_uid)
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
                    detail="question_followups 必须完整覆盖岗位题目，且 max_followups 取值范围为 0-3",
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
                "created_at": detail["created_at"],
                "completed_at": detail["completed_at"],
                "completed_reason": detail.get("completed_reason"),
                "job": detail["job"],
                "selected_questions": detail.get("selected_questions", []),
                "required_checkins": detail.get("required_checkins", []),
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

    @app.get("/api/public/interviews/{token}/access")
    async def public_interview_access(token: str) -> Dict[str, Any]:
        detail = get_public_access(token)
        if not detail:
            raise HTTPException(status_code=404, detail="面试链接无效或已失效")
        return {"interview": detail}

    return app
