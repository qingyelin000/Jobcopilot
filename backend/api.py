import asyncio
from contextlib import AsyncExitStack
from datetime import datetime
import hashlib
from io import BytesIO
import uuid
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import agents
import os
import json
import requests
from document_assets import mark_interrupted_document_jobs, router as document_router
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from PyPDF2 import PdfReader

from langchain_openai import ChatOpenAI
# MCP 核心连接组件
from mcp import ClientSession
from mcp.client.sse import sse_client
from intent_agent import llm_build_intent_plan, llm_evaluate_completion
from auth import create_access_token, decode_access_token, get_password_hash, verify_password
from auth_schemas import (
    LoginRequest,
    PasswordChangeRequest,
    PasswordChangeResponse,
    RegisterRequest,
    TokenResponse,
    UserPreferenceUpdate,
    UserProfileResponse,
    UserProfileUpdate,
)
from db import Base, SessionLocal, engine, get_db
from models import JDDocument, ResumeDocument, ResumeProcessJob, User
from schemas import JDInfo, UserInfo

app = FastAPI(title="JobCopilot API Backend")

frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8501,http://127.0.0.1:8501",
    ).split(",")
    if origin.strip()
]


def _feature_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(document_router)


def _ensure_user_profile_columns() -> None:
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    alter_statements = {
        "full_name": "ALTER TABLE users ADD COLUMN full_name VARCHAR(120) NULL",
        "email": "ALTER TABLE users ADD COLUMN email VARCHAR(120) NULL",
        "phone": "ALTER TABLE users ADD COLUMN phone VARCHAR(40) NULL",
        "city": "ALTER TABLE users ADD COLUMN city VARCHAR(80) NULL",
        "target_role": "ALTER TABLE users ADD COLUMN target_role VARCHAR(120) NULL",
        "profile_summary": "ALTER TABLE users ADD COLUMN profile_summary TEXT NULL",
    }

    with engine.begin() as connection:
        for column_name, statement in alter_statements.items():
            if column_name in existing_columns:
                continue
            connection.execute(text(statement))


def _serialize_user_profile(user: User) -> UserProfileResponse:
    return UserProfileResponse(
        id=user.id,
        username=user.username,
        location_consent=user.location_consent,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        city=user.city,
        target_role=user.target_role,
        profile_summary=user.profile_summary,
    )


@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    _ensure_user_profile_columns()
    _mark_interrupted_process_jobs()
    mark_interrupted_document_jobs()

class ProcessRequest(BaseModel):
    resume_text: str
    jd_text: str


class ProcessJobResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    progress: int
    message: str
    data: dict | None = None
    error: str | None = None


class ProcessHistoryItemResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    progress: int
    message: str
    headline: str
    subtitle: str | None = None
    created_at: datetime
    updated_at: datetime

class ParsePdfResponse(BaseModel):
    filename: str
    page_count: int
    char_count: int
    text: str

class ChatRequest(BaseModel):
    message: str
    location_consent: bool | None = None
    consent_scope: str | None = None
    user_city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def _normalize_city(city: str | None) -> str:
    if not city:
        return ""
    return str(city).strip().replace("市", "")


def _reverse_geocode_by_amap(latitude: float, longitude: float) -> str:
    key = os.getenv("GEO_AMAP_KEY", "").strip()
    if not key:
        return ""
    try:
        response = requests.get(
            "https://restapi.amap.com/v3/geocode/regeo",
            params={
                "key": key,
                "location": f"{longitude},{latitude}",
                "extensions": "base",
            },
            timeout=5,
        )
        data = response.json()
        if data.get("status") != "1":
            return ""
        address = data.get("regeocode", {}).get("addressComponent", {})
        city = _normalize_city(address.get("city"))
        if city:
            return city
        return _normalize_city(address.get("province"))
    except Exception:
        return ""


def _reverse_geocode_by_tencent(latitude: float, longitude: float) -> str:
    key = os.getenv("GEO_TENCENT_KEY", "").strip()
    if not key:
        return ""
    try:
        response = requests.get(
            "https://apis.map.qq.com/ws/geocoder/v1/",
            params={
                "key": key,
                "location": f"{latitude},{longitude}",
            },
            timeout=5,
        )
        data = response.json()
        if data.get("status") != 0:
            return ""
        address = data.get("result", {}).get("address_component", {})
        city = _normalize_city(address.get("city"))
        if city:
            return city
        return _normalize_city(address.get("province"))
    except Exception:
        return ""


def _resolve_city_from_coordinates(latitude: float | None, longitude: float | None) -> str:
    if latitude is None or longitude is None:
        return ""
    city = _reverse_geocode_by_amap(latitude, longitude)
    if city:
        return city
    return _reverse_geocode_by_tencent(latitude, longitude)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix):].strip()
    return None


def get_current_user_optional(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    token = _extract_bearer_token(authorization)
    if not token:
        return None

    username = decode_access_token(token)
    if not username:
        return None

    return db.query(User).filter(User.username == username).first()


def get_current_user(
    current_user: User | None = Depends(get_current_user_optional),
) -> User:
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return current_user

def _mcp_result_to_text(result) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        texts = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                texts.append(text)
        if texts:
            return "\n".join(texts)
    return str(result)


async def call_mcp_tool(session: ClientSession, tool_name: str, arguments: dict) -> str:
    result = await session.call_tool(tool_name, arguments=arguments)
    return _mcp_result_to_text(result)


def _extract_text_from_pdf_bytes(file_bytes: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(file_bytes))
    pages: list[str] = []

    for page in reader.pages:
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(page_text)

    return "\n\n".join(pages).strip(), len(reader.pages)


def _build_process_cache_key(resume_text: str, jd_text: str) -> str:
    payload = f"{resume_text}\n---JD---\n{jd_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_content_hash(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


_UNSET = object()


def _clone_process_data(data: dict | None) -> dict | None:
    if data is None:
        return None
    return json.loads(json.dumps(data, ensure_ascii=False))


def _hydrate_jd_info_payload(payload: dict | None, jd_text: str) -> dict | None:
    if not isinstance(payload, dict):
        return payload
    if not jd_text.strip():
        return payload

    raw_jd = payload.get("jd_info")
    if not isinstance(raw_jd, dict):
        return payload

    try:
        jd_info = JDInfo.model_validate(raw_jd)
    except Exception:
        return payload

    enriched = agents.enrich_jd_info(jd_info, jd_text)
    if enriched.job_title == jd_info.job_title and enriched.business_domain == jd_info.business_domain:
        return payload

    next_payload = _clone_process_data(payload) or {}
    next_payload["jd_info"] = enriched.model_dump()
    return next_payload


def _payload_has_jd_title(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    raw_jd = payload.get("jd_info")
    if not isinstance(raw_jd, dict):
        return False
    return bool(str(raw_jd.get("job_title") or "").strip())


def _serialize_process_job(job: ResumeProcessJob) -> dict:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "message": job.message,
        "data": _clone_process_data(job.data),
        "error": job.error,
    }


def _compact_text(value: str | None, fallback: str | None = None, limit: int = 68) -> str | None:
    text = (value or fallback or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _serialize_process_history_item(job: ResumeProcessJob) -> dict:
    data = job.data or {}
    jd_info = data.get("jd_info") or {}
    match_mapping = data.get("match_mapping") or {}
    optimized_resume = data.get("optimized_resume") or {}

    headline = _compact_text(
        jd_info.get("job_title"),
        match_mapping.get("candidate_positioning") or "简历优化任务",
        limit=40,
    ) or "简历优化任务"
    subtitle = _compact_text(
        jd_info.get("business_domain"),
        optimized_resume.get("summary_hook") or job.message,
        limit=72,
    )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "message": job.message,
        "headline": headline,
        "subtitle": subtitle,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _scoped_process_job_query(db: Session, user_id: int | None):
    query = db.query(ResumeProcessJob)
    if user_id is None:
        return query.filter(ResumeProcessJob.user_id.is_(None))
    return query.filter(ResumeProcessJob.user_id == user_id)


def _mark_interrupted_process_jobs() -> None:
    db = SessionLocal()
    try:
        running_jobs = db.query(ResumeProcessJob).filter(ResumeProcessJob.status == "running").all()
        if not running_jobs:
            return

        for job in running_jobs:
            job.status = "error"
            job.stage = "error"
            job.progress = 100
            job.message = "服务已重启，请重新发起简历优化"
            job.error = "server_restarted"

        db.commit()
    finally:
        db.close()


def _get_cached_process_result(cache_key: str, user_id: int | None) -> dict | None:
    db = SessionLocal()
    try:
        job = (
            _scoped_process_job_query(db, user_id)
            .filter(
                ResumeProcessJob.cache_key == cache_key,
                ResumeProcessJob.status == "success",
            )
            .order_by(ResumeProcessJob.updated_at.desc())
            .first()
        )
        if job is None or job.data is None:
            return None

        return {
            "status": "success",
            "data": _clone_process_data(job.data),
        }
    finally:
        db.close()


def _get_cached_process_job(cache_key: str, user_id: int | None) -> dict | None:
    db = SessionLocal()
    try:
        job = (
            _scoped_process_job_query(db, user_id)
            .filter(
                ResumeProcessJob.cache_key == cache_key,
                ResumeProcessJob.status == "success",
            )
            .order_by(ResumeProcessJob.updated_at.desc())
            .first()
        )
        if job is None:
            return None

        return _serialize_process_job(job)
    finally:
        db.close()


def _get_running_process_job(cache_key: str, user_id: int | None) -> dict | None:
    db = SessionLocal()
    try:
        job = (
            _scoped_process_job_query(db, user_id)
            .filter(
                ResumeProcessJob.cache_key == cache_key,
                ResumeProcessJob.status == "running",
            )
            .order_by(ResumeProcessJob.updated_at.desc())
            .first()
        )
        if job is None:
            return None

        return _serialize_process_job(job)
    finally:
        db.close()


def _get_process_job(job_id: str, user_id: int | None) -> dict | None:
    db = SessionLocal()
    try:
        job = (
            _scoped_process_job_query(db, user_id)
            .filter(ResumeProcessJob.job_id == job_id)
            .first()
        )
        if job is None:
            return None

        return _serialize_process_job(job)
    finally:
        db.close()


def _find_ready_document_for_text(
    db: Session,
    model,
    user_id: int,
    source_text: str,
):
    content_hash = _build_content_hash(source_text)
    document = (
        db.query(model)
        .filter(
            model.user_id == user_id,
            model.content_hash == content_hash,
            model.status == "ready",
        )
        .order_by(model.updated_at.desc(), model.id.desc())
        .first()
    )
    if document is not None:
        return document

    normalized_text = source_text.strip()
    if not normalized_text:
        return None

    candidates = (
        db.query(model)
        .filter(
            model.user_id == user_id,
            model.status == "ready",
        )
        .order_by(model.updated_at.desc(), model.id.desc())
        .all()
    )
    for candidate in candidates:
        if (candidate.source_text or "").strip() == normalized_text:
            return candidate

    return None


def _load_cached_resume_user_info(resume_text: str, user_id: int | None) -> UserInfo | None:
    if user_id is None:
        return None

    db = SessionLocal()
    try:
        document = _find_ready_document_for_text(db, ResumeDocument, user_id, resume_text)
        if document is None or not isinstance(document.parsed_json, dict):
            return None

        try:
            return UserInfo.model_validate(document.parsed_json)
        except Exception:
            return None
    finally:
        db.close()


def _load_cached_jd_info(jd_text: str, user_id: int | None) -> JDInfo | None:
    if user_id is None:
        return None

    db = SessionLocal()
    try:
        document = _find_ready_document_for_text(db, JDDocument, user_id, jd_text)
        if document is None or not isinstance(document.parsed_json, dict):
            return None

        try:
            jd_info = JDInfo.model_validate(document.parsed_json)
            return agents.enrich_jd_info(
                jd_info,
                jd_text,
                title_hint=document.title,
            )
        except Exception:
            return None
    finally:
        db.close()


def _create_process_job(cache_key: str, user_id: int | None) -> dict:
    db = SessionLocal()
    try:
        job = ResumeProcessJob(
            job_id=uuid.uuid4().hex,
            user_id=user_id,
            cache_key=cache_key,
            status="running",
            stage="parsing",
            progress=12,
            message="正在解析简历与 JD",
            data={},
            error=None,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return _serialize_process_job(job)
    finally:
        db.close()


def _create_completed_process_job(cache_key: str, user_id: int | None, data: dict) -> dict:
    db = SessionLocal()
    try:
        job = ResumeProcessJob(
            job_id=uuid.uuid4().hex,
            user_id=user_id,
            cache_key=cache_key,
            status="success",
            stage="done",
            progress=100,
            message="简历分析与优化已完成",
            data=_clone_process_data(data),
            error=None,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return _serialize_process_job(job)
    finally:
        db.close()


def _update_process_job(
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    data: dict | None = None,
    error: str | None | object = _UNSET,
) -> dict:
    db = SessionLocal()
    try:
        job = db.query(ResumeProcessJob).filter(ResumeProcessJob.job_id == job_id).first()
        if job is None:
            raise ValueError(f"Process job {job_id} not found")

        if status is not None:
            job.status = status
        if stage is not None:
            job.stage = stage
        if progress is not None:
            job.progress = progress
        if message is not None:
            job.message = message
        if data:
            merged_data = _clone_process_data(job.data) or {}
            merged_data.update(_clone_process_data(data) or {})
            job.data = merged_data
        if error is not _UNSET:
            job.error = error

        db.add(job)
        db.commit()
        db.refresh(job)
        return _serialize_process_job(job)
    finally:
        db.close()


async def _build_process_payload(
    req: ProcessRequest,
    job_id: str | None = None,
    user_id: int | None = None,
) -> dict:
    user_info = _load_cached_resume_user_info(req.resume_text, user_id)
    jd_info = _load_cached_jd_info(req.jd_text, user_id)
    reused_resume = user_info is not None
    reused_jd = jd_info is not None

    user_info_task = None if user_info is not None else asyncio.to_thread(agents.parse_resume_to_json, req.resume_text)
    jd_info_task = None if jd_info is not None else asyncio.to_thread(agents.parse_jd_to_json, req.jd_text)

    if user_info_task is not None and jd_info_task is not None:
        user_info, jd_info = await asyncio.gather(user_info_task, jd_info_task)
    elif user_info_task is not None:
        user_info = await user_info_task
    elif jd_info_task is not None:
        jd_info = await jd_info_task

    assert user_info is not None
    assert jd_info is not None

    if job_id is not None:
        parse_done_message = "解析完成，正在生成匹配摘要"
        if reused_resume and reused_jd:
            parse_done_message = "已复用简历与 JD 解析结果，正在生成匹配摘要"
        elif reused_resume:
            parse_done_message = "已复用简历解析结果，正在生成匹配摘要"
        elif reused_jd:
            parse_done_message = "已复用 JD 解析结果，正在生成匹配摘要"

        _update_process_job(
            job_id,
            stage="mapping",
            progress=42,
            message=parse_done_message,
            data={
                "user_info": user_info.model_dump(),
                "jd_info": jd_info.model_dump(),
            },
        )

    match_mapping = await asyncio.to_thread(agents.map_resume_to_jd, user_info, jd_info)
    mapping_quality = await asyncio.to_thread(
        agents.score_mapping_quality,
        user_info,
        jd_info,
        match_mapping,
    )

    if job_id is not None:
        _update_process_job(
            job_id,
            stage="rewriting",
            progress=74,
            message="匹配摘要已生成，正在重写项目表述",
            data={
                "match_mapping": match_mapping.model_dump(),
                "mapping_quality": mapping_quality,
            },
        )

    optimized_resume = await asyncio.to_thread(
        agents.rewrite_resume_bullets,
        user_info,
        jd_info,
        match_mapping,
    )
    rewrite_quality = await asyncio.to_thread(
        agents.score_rewrite_quality,
        user_info,
        jd_info,
        match_mapping,
        optimized_resume,
    )

    if job_id is not None:
        _update_process_job(
            job_id,
            stage="rewriting",
            progress=92,
            message="改写完成，正在计算质量评分。",
            data={
                "optimized_resume": optimized_resume.model_dump(),
                "rewrite_quality": rewrite_quality,
            },
        )

    return {
        "status": "success",
        "data": {
            "user_info": user_info.model_dump(),
            "jd_info": jd_info.model_dump(),
            "match_mapping": match_mapping.model_dump(),
            "optimized_resume": optimized_resume.model_dump(),
            "mapping_quality": mapping_quality,
            "rewrite_quality": rewrite_quality,
        },
    }


async def _run_process_job(job_id: str, req: ProcessRequest, user_id: int | None) -> None:
    try:
        response_payload = await _build_process_payload(req, job_id=job_id, user_id=user_id)
        _update_process_job(
            job_id,
            status="success",
            stage="done",
            progress=100,
            message="简历分析与优化已完成",
            data=response_payload["data"],
            error=None,
        )
    except Exception as exc:
        _update_process_job(
            job_id,
            status="error",
            stage="error",
            progress=100,
            message="生成失败，请重试",
            error=str(exc),
        )


@app.post("/api/v1/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == req.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    new_user = User(
        username=req.username,
        password_hash=get_password_hash(req.password),
        location_consent=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(new_user.username)
    return TokenResponse(access_token=token)


@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.username)
    return TokenResponse(access_token=token)


@app.get("/api/v1/users/me", response_model=UserProfileResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return _serialize_user_profile(current_user)


@app.patch("/api/v1/users/me/preferences", response_model=UserProfileResponse)
def update_preferences(
    req: UserPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.location_consent = req.location_consent
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    return _serialize_user_profile(current_user)


@app.patch("/api/v1/users/me/profile", response_model=UserProfileResponse)
def update_user_profile(
    req: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.full_name = req.full_name.strip() if req.full_name is not None else None
    current_user.email = req.email.strip() if req.email is not None else None
    current_user.phone = req.phone.strip() if req.phone is not None else None
    current_user.city = req.city.strip() if req.city is not None else None
    current_user.target_role = req.target_role.strip() if req.target_role is not None else None
    current_user.profile_summary = req.profile_summary.strip() if req.profile_summary is not None else None
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return _serialize_user_profile(current_user)


@app.patch("/api/v1/users/me/password", response_model=PasswordChangeResponse)
def update_user_password(
    req: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码错误")

    if req.current_password == req.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    current_user.password_hash = get_password_hash(req.new_password)
    db.add(current_user)
    db.commit()
    return PasswordChangeResponse(success=True, message="密码修改成功")


@app.post("/api/v1/resume/parse-pdf", response_model=ParsePdfResponse)
async def parse_resume_pdf(file: UploadFile = File(...)):
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件")

    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="上传的 PDF 为空")

        text, page_count = await asyncio.to_thread(_extract_text_from_pdf_bytes, file_bytes)
        if not text:
            raise HTTPException(status_code=400, detail="PDF 中没有可提取的文本")

        return ParsePdfResponse(
            filename=filename,
            page_count=page_count,
            char_count=len(text),
            text=text,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 解析失败: {exc}") from exc

@app.post("/api/v1/process")
async def process_job_application(
    req: ProcessRequest,
    current_user: User | None = Depends(get_current_user_optional),
):
    """Synchronous compatibility endpoint for the full resume optimization flow."""
    try:
        cache_key = _build_process_cache_key(req.resume_text, req.jd_text)
        user_id = current_user.id if current_user else None
        cached_response = _get_cached_process_result(cache_key, user_id)
        if cached_response is not None:
            hydrated_data = _hydrate_jd_info_payload(cached_response.get("data"), req.jd_text)
            response_with_hydration = (
                cached_response
                if hydrated_data is cached_response.get("data")
                else {
                    "status": cached_response.get("status", "success"),
                    "data": hydrated_data,
                }
            )

            if _payload_has_jd_title(response_with_hydration.get("data")):
                return response_with_hydration

            cached_doc_jd = _load_cached_jd_info(req.jd_text, user_id)
            if cached_doc_jd is None or not str(cached_doc_jd.job_title or "").strip():
                return response_with_hydration

            # Legacy cached result misses JD title; rebuild once to refresh output.
            cached_response = None

        response_payload = await _build_process_payload(req, user_id=user_id)
        _create_completed_process_job(cache_key, user_id, response_payload["data"])
        return response_payload
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/process/start", response_model=ProcessJobResponse)
async def start_process_job(
    req: ProcessRequest,
    current_user: User | None = Depends(get_current_user_optional),
):
    cache_key = _build_process_cache_key(req.resume_text, req.jd_text)
    user_id = current_user.id if current_user else None

    cached_job = _get_cached_process_job(cache_key, user_id)
    if cached_job is not None:
        hydrated_data = _hydrate_jd_info_payload(cached_job.get("data"), req.jd_text)
        next_job = cached_job if hydrated_data is cached_job.get("data") else {**cached_job, "data": hydrated_data}

        if _payload_has_jd_title(next_job.get("data")):
            return next_job

        cached_doc_jd = _load_cached_jd_info(req.jd_text, user_id)
        if cached_doc_jd is None or not str(cached_doc_jd.job_title or "").strip():
            return next_job
        # Legacy cached job misses JD title; trigger a fresh run.

    running_job = _get_running_process_job(cache_key, user_id)
    if running_job is not None:
        return running_job

    job = _create_process_job(cache_key, user_id)
    asyncio.create_task(_run_process_job(job["job_id"], req, user_id))
    return job


@app.get("/api/v1/process/history", response_model=list[ProcessHistoryItemResponse])
async def get_process_history(
    limit: int = 8,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    safe_limit = max(1, min(limit, 20))
    jobs = (
        db.query(ResumeProcessJob)
        .filter(ResumeProcessJob.user_id == current_user.id)
        .order_by(ResumeProcessJob.updated_at.desc())
        .limit(safe_limit)
        .all()
    )
    return [_serialize_process_history_item(job) for job in jobs]


@app.get("/api/v1/process/{job_id}", response_model=ProcessJobResponse)
async def get_process_job(
    job_id: str,
    current_user: User | None = Depends(get_current_user_optional),
):
    user_id = current_user.id if current_user else None
    job = _get_process_job(job_id, user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    return job


@app.delete("/api/v1/process/{job_id}")
async def delete_process_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = (
        db.query(ResumeProcessJob)
        .filter(
            ResumeProcessJob.job_id == job_id,
            ResumeProcessJob.user_id == current_user.id,
        )
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if job.status == "running":
        raise HTTPException(status_code=409, detail="任务正在处理中，暂时无法删除")

    db.delete(job)
    db.commit()
    return {"success": True}


@app.post("/api/v1/chat")
async def chat_with_agent(
    req: ChatRequest,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    if not _feature_enabled("ENABLE_CHAT", True):
        raise HTTPException(status_code=404, detail="聊天功能暂未开放")

    # 意图驱动聊天接口：先判意图，再决定调用哪些工具与顺序
    try:
        # 获取环境变量中 MCP 爬虫服务器的地址
        mcp_url = os.environ.get("MCP_SERVER_URL", "http://mcp_crawler:8001/sse")
        
        async with AsyncExitStack() as stack:
            # 1. 建立与爬虫微服务节点的 SSE 远程连接！
            sse_transport = await stack.enter_async_context(sse_client(mcp_url))
            session = await stack.enter_async_context(ClientSession(sse_transport[0], sse_transport[1]))
            await session.initialize()
            
            # 2. 动态发现远程能用的绝技 (Tools)
            mcp_response = await session.list_tools()
            available_tools = {tool.name for tool in mcp_response.tools}
            
            # 3. 初始化绑定了 DeepSeek 的大模型
            llm = ChatOpenAI(
                api_key=os.environ.get('DEEPSEEK_API_KEY'),
                base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.deepseek.com'),
                model='deepseek-chat',
                temperature=0.2,
            )

            # 4. 意图Agent(LLM)先判断：调用什么工具、如何执行、执行顺序
            intent_plan = await llm_build_intent_plan(req.message, sorted(list(available_tools)), llm)

            execution_steps = [step for step in intent_plan.execution_steps if step.tool_name in available_tools]
            tool_outputs = []
            city_name = _normalize_city(req.user_city)
            if not city_name:
                city_name = _resolve_city_from_coordinates(req.latitude, req.longitude)

            scope = (req.consent_scope or "").strip().lower()
            should_persist_consent = scope == "always"
            effective_location_consent = (
                req.location_consent
                if req.location_consent is not None
                else (current_user.location_consent if current_user else False)
            )

            if req.location_consent is not None and current_user and should_persist_consent:
                current_user.location_consent = req.location_consent
                db.add(current_user)
                db.commit()
                db.refresh(current_user)

            # 5. 按计划执行工具，并在每步后评估是否完成
            max_steps = min(len(execution_steps), 5)
            for index in range(max_steps):
                step = execution_steps[index]
                if step.tool_name == 'get_user_location':
                    location_text = await call_mcp_tool(
                        session,
                        'get_user_location',
                        {
                            'consent': effective_location_consent,
                            'user_city': req.user_city or '',
                        },
                    )
                    tool_outputs.append({'tool': 'get_user_location', 'output': location_text})

                    if '未获得用户定位授权' in location_text:
                        return {'reply': location_text, 'need_location_consent': True}

                    if not city_name:
                        city_name = location_text.strip().replace('市', '')

                elif step.tool_name == 'crawl_nearby_jobs':
                    if not city_name:
                        return {'reply': '要帮你找附近工作，我需要你的城市信息或定位授权。'}

                    step_args = dict(step.arguments or {})
                    step_args.setdefault('keyword', 'Python')
                    step_args.setdefault('num_pages', 1)
                    step_args['city_name'] = city_name

                    crawler_text = await call_mcp_tool(
                        session,
                        'crawl_nearby_jobs',
                        step_args,
                    )
                    tool_outputs.append({'tool': 'crawl_nearby_jobs', 'output': crawler_text})

                else:
                    generic_text = await call_mcp_tool(
                        session,
                        step.tool_name,
                        dict(step.arguments or {}),
                    )
                    tool_outputs.append({'tool': step.tool_name, 'output': generic_text})

                evaluation = await llm_evaluate_completion(req.message, intent_plan, tool_outputs, llm)
                if evaluation.is_complete or not evaluation.should_continue:
                    break

            # 6. 组织最终回复
            if tool_outputs:
                final_prompt = (
                    '你是 JobCopilot 求职助手。请根据用户问题、意图计划和工具结果给出简洁、结构化回答。\n'
                    f'用户问题: {req.message}\n'
                    f'意图计划: {intent_plan.model_dump_json(ensure_ascii=False)}\n'
                    f'工具输出: {json.dumps(tool_outputs, ensure_ascii=False)}\n'
                    '请输出中文结果，若工具失败需给出下一步建议。'
                )
                final_msg = await llm.ainvoke(final_prompt)
                return {'reply': final_msg.content}

            # 无需工具时直接聊天回答
            normal_msg = await llm.ainvoke(
                f"你是 JobCopilot 求职助手，请直接回答用户问题：{req.message}"
            )
            return {'reply': normal_msg.content}

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

