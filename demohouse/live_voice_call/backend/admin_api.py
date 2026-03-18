import csv
import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

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

def _load_cors_origins() -> List[str]:
    raw = os.getenv("ADMIN_CORS_ORIGINS")
    if raw and raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ]


def build_interview_link(token: str) -> str:
    base = (os.getenv("PUBLIC_INTERVIEW_BASE_URL") or "http://localhost:8080/check-in").strip()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}token={token}"


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

    # The first row is always treated as header and ignored.
    try:
        next(reader)
    except StopIteration:
        raise HTTPException(status_code=400, detail="CSV 缺少表头")

    rows: List[Dict[str, str]] = []
    for row in reader:
        if not row or all(not str(cell).strip() for cell in row):
            continue

        question = (row[0] if len(row) >= 1 else "").strip()
        answer = (row[1] if len(row) >= 2 else "").strip()

        if not question:
            continue

        rows.append({"question": question, "reference_answer": answer})

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


class CreateInterviewBody(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=100)
    job_uid: str = Field(min_length=1)
    duration_minutes: int = Field(ge=5, le=180)
    notes: Optional[str] = Field(default=None, max_length=1000)


def create_admin_app() -> FastAPI:
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
            questions=[(r["question"], r["reference_answer"]) for r in rows],
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

    @app.post("/api/admin/interviews")
    async def post_interview(body: CreateInterviewBody, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        try:
            interview = create_interview(
                candidate_name=body.candidate_name.strip(),
                job_uid=body.job_uid.strip(),
                duration_minutes=body.duration_minutes,
                notes=(body.notes or "").strip() or None,
            )
        except ValueError as e:
            if str(e) == "job_not_found":
                raise HTTPException(status_code=404, detail="岗位不存在")
            if str(e) == "job_question_bank_empty":
                raise HTTPException(status_code=400, detail="岗位题库为空")
            raise
        interview["interview_link"] = build_interview_link(interview["token"])
        return {"interview": interview}

    @app.get("/api/admin/interviews/{token}")
    async def get_interview(token: str, _admin: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
        detail = get_interview_detail(token)
        if not detail:
            raise HTTPException(status_code=404, detail="面试不存在")

        completed = detail["status"] == "completed"
        response: Dict[str, Any] = {
            "interview": {
                "token": detail["token"],
                "candidate_name": detail["candidate_name"],
                "duration_minutes": detail["duration_minutes"],
                "question_count": detail["question_count"],
                "notes": detail["notes"],
                "status": detail["status"],
                "created_at": detail["created_at"],
                "completed_at": detail["completed_at"],
                "job": detail["job"],
                "selected_questions": detail.get("selected_questions", []),
                "interview_link": build_interview_link(detail["token"]),
                "completed": completed,
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
