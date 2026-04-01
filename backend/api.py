import asyncio
from datetime import datetime
import hashlib
from io import BytesIO
from threading import Lock
from typing import Any
import uuid
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import agents
import os
import json
from document_assets import mark_interrupted_document_jobs, router as document_router
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from PyPDF2 import PdfReader

from langchain_openai import ChatOpenAI
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
from interview.embedding_utils import default_embedding_provider_name
from interview.retriever_v2 import DEFAULT_DATASET_PATH, serialize_retrieved_question
from models import (
    JDDocument,
    InterviewSession,
    InterviewTurn,
    ResumeDocument,
    ResumeProcessJob,
    User,
)
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


class InterviewSessionStartRequest(BaseModel):
    resume_id: int = Field(ge=1)
    jd_id: int = Field(ge=1)
    backend: str | None = None
    strict_metadata_filter: bool | None = None


class InterviewSessionStartResponse(BaseModel):
    session_id: str
    status: str
    backend: str
    current_round: int
    max_rounds: int
    question: dict[str, Any]


class InterviewAnswerRequest(BaseModel):
    answer_text: str = Field(min_length=1)


class InterviewAnswerResponse(BaseModel):
    session_id: str
    status: str
    current_round: int
    max_rounds: int
    evaluation: dict[str, Any]
    next_question: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None


class InterviewSummaryResponse(BaseModel):
    session_id: str
    status: str
    current_round: int
    max_rounds: int
    summary: dict[str, Any]
    turns: list[dict[str, Any]]


DEFAULT_RETRIEVER_BACKEND = (
    os.getenv("RETRIEVER_BACKEND", "v2").strip().lower() or "v2"
)
_RETRIEVER_CACHE: dict[str, Any] = {}
_RETRIEVER_LOCK = Lock()


class InterviewRetrieveRequest(BaseModel):
    query: str = ""
    resume_text: str = ""
    jd_text: str = ""
    top_k: int = Field(default=20, ge=1, le=20)
    target_company: str | None = None
    target_role: str | None = None
    backend: str | None = None
    strict_metadata_filter: bool | None = None


class InterviewRetrieveResponse(BaseModel):
    backend: str
    top_k: int
    result_count: int
    results: list[dict[str, Any]]


def _safe_int_from_env(name: str, default: int, minimum: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not str(raw_value).strip():
        value = int(default)
    else:
        try:
            value = int(str(raw_value).strip())
        except ValueError:
            value = int(default)
    if minimum is not None:
        value = max(value, int(minimum))
    return value


def _safe_float_from_env(name: str, default: float, minimum: float | None = None) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not str(raw_value).strip():
        value = float(default)
    else:
        try:
            value = float(str(raw_value).strip())
        except ValueError:
            value = float(default)
    if minimum is not None:
        value = max(value, float(minimum))
    return value


INTERVIEW_TOP_K = min(_safe_int_from_env("INTERVIEW_TOP_K", 20, minimum=1), 20)
INTERVIEW_MAX_ROUNDS = _safe_int_from_env("INTERVIEW_MAX_ROUNDS", 0, minimum=0)


def _normalize_retriever_backend(value: str | None) -> str:
    backend = (value or DEFAULT_RETRIEVER_BACKEND or "v2").strip().lower()
    if backend in {"", "v2", "v1"}:
        # v1 has been sunset; keep compatibility by routing to v2.
        return "v2"
    raise ValueError("Invalid backend. Use: v2.")


def _load_ready_resume_document(
    db: Session,
    *,
    user_id: int,
    resume_id: int,
) -> ResumeDocument:
    document = (
        db.query(ResumeDocument)
        .filter(
            ResumeDocument.id == resume_id,
            ResumeDocument.user_id == user_id,
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Resume document not found.")
    if document.status != "ready":
        raise HTTPException(status_code=409, detail="Selected resume is not ready yet.")
    if not str(document.source_text or "").strip():
        raise HTTPException(status_code=409, detail="Selected resume has empty content.")
    return document


def _load_ready_jd_document(
    db: Session,
    *,
    user_id: int,
    jd_id: int,
) -> JDDocument:
    document = (
        db.query(JDDocument)
        .filter(
            JDDocument.id == jd_id,
            JDDocument.user_id == user_id,
        )
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="JD document not found.")
    if document.status != "ready":
        raise HTTPException(status_code=409, detail="Selected JD is not ready yet.")
    if not str(document.source_text or "").strip():
        raise HTTPException(status_code=409, detail="Selected JD has empty content.")
    return document


def _resolve_session_targets(
    *,
    current_user: User,
    jd_document: JDDocument,
) -> tuple[str, str]:
    parsed_jd = jd_document.parsed_json if isinstance(jd_document.parsed_json, dict) else {}
    target_company = str(parsed_jd.get("company_name") or "").strip()
    target_role = str(parsed_jd.get("job_title") or "").strip()

    if not target_role:
        target_role = str(current_user.target_role or "").strip()
    return target_company, target_role


def _compose_interview_query(
    *,
    target_company: str,
    target_role: str,
    jd_title: str,
) -> str:
    query_parts: list[str] = []
    for value in (target_company, target_role, jd_title):
        item = str(value or "").strip()
        if item and item not in query_parts:
            query_parts.append(item)
    return " ".join(query_parts)


def _build_retriever() -> Any:
    from interview.retriever_v2 import DEFAULT_QDRANT_COLLECTION, DEFAULT_QDRANT_URL, RetrieverV2

    dataset_path = (
        os.getenv("RETRIEVER_DATASET_PATH", str(DEFAULT_DATASET_PATH)).strip()
        or str(DEFAULT_DATASET_PATH)
    )
    qdrant_url = os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).strip() or DEFAULT_QDRANT_URL
    if os.path.exists("/.dockerenv"):
        qdrant_url = qdrant_url.replace("://localhost", "://qdrant").replace("://127.0.0.1", "://qdrant")
    embedding_provider = (
        os.getenv("EMBEDDING_PROVIDER", "").strip().lower()
        or default_embedding_provider_name()
    )
    return RetrieverV2(
        dataset_path=dataset_path,
        qdrant_url=qdrant_url,
        qdrant_api_key=os.getenv("QDRANT_API_KEY", "").strip() or None,
        collection_name=os.getenv("QDRANT_COLLECTION", DEFAULT_QDRANT_COLLECTION).strip()
        or DEFAULT_QDRANT_COLLECTION,
        embedding_provider=embedding_provider,
        embedding_model=os.getenv("EMBEDDING_MODEL", "").strip() or None,
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", "").strip() or None,
        embedding_base_url=os.getenv("EMBEDDING_BASE_URL", "").strip() or None,
        embedding_dimension=_safe_int_from_env("EMBEDDING_DIMENSION", 384, minimum=1),
        vector_candidate_pool=_safe_int_from_env("RETRIEVER_V2_VECTOR_POOL", 64, minimum=8),
        lexical_candidate_pool=_safe_int_from_env("RETRIEVER_V2_LEXICAL_POOL", 40, minimum=8),
        strict_metadata_filter=_feature_enabled("RETRIEVER_V2_STRICT_METADATA_FILTER", default=False),
        rerank_enabled=_feature_enabled("RERANK_ENABLED", default=True),
        rerank_model=os.getenv("RERANK_MODEL", "").strip() or None,
        rerank_api_key=os.getenv("RERANK_API_KEY", "").strip() or None,
        rerank_base_url=os.getenv("RERANK_BASE_URL", "").strip() or None,
        rerank_timeout_seconds=_safe_float_from_env("RERANK_TIMEOUT_SECONDS", 15.0, minimum=1.0),
        rerank_candidate_pool=_safe_int_from_env("RERANK_CANDIDATE_POOL", 40, minimum=8),
    )


def _get_retriever(backend: str) -> Any:
    with _RETRIEVER_LOCK:
        cached = _RETRIEVER_CACHE.get(backend)
        if cached is not None:
            return cached
        retriever = _build_retriever()
        _RETRIEVER_CACHE[backend] = retriever
        return retriever


def _normalize_question_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": str(payload.get("question_id") or "").strip(),
        "source_content_id": str(payload.get("source_content_id") or "").strip(),
        "company": str(payload.get("company") or "").strip(),
        "role": str(payload.get("role") or "").strip(),
        "section": payload.get("section"),
        "publish_time": payload.get("publish_time"),
        "question_text": str(payload.get("question_text") or "").strip(),
        "question_type": str(payload.get("question_type") or "general").strip() or "general",
    }


def _fallback_interview_question(candidates: list[dict[str, Any]], *, turn_index: int) -> dict[str, Any]:
    if candidates:
        first = _normalize_question_payload(candidates[0])
        first["ask_mode"] = "new_question"
        first["reason"] = "fallback_first_candidate"
        return first
    return {
        "question_id": f"generated::fallback::{turn_index}",
        "source_content_id": "",
        "company": "",
        "role": "",
        "section": None,
        "publish_time": None,
        "question_text": "请你介绍一个最有挑战的项目，并重点说明技术难点与解决方案。",
        "question_type": "project_or_system_design",
        "ask_mode": "new_question",
        "reason": "fallback_default_question",
    }


def _pick_interviewer_question(
    *,
    query: str,
    target_company: str,
    target_role: str,
    resume_text: str,
    jd_text: str,
    candidate_questions: list[dict[str, Any]],
    history_turns: list[dict[str, Any]],
    follow_up_hint: str | None = None,
    turn_index: int,
) -> dict[str, Any]:
    asked_ids = {
        str((item.get("question") or {}).get("question_id") or "").strip()
        for item in history_turns
    }
    available = [
        _normalize_question_payload(item)
        for item in candidate_questions
        if str(item.get("question_id") or "").strip() not in asked_ids
    ]
    if not available:
        available = [_normalize_question_payload(item) for item in candidate_questions]

    try:
        picked = agents.interviewer_agent_pick_question(
            query=query,
            target_company=target_company,
            target_role=target_role,
            resume_text=resume_text,
            jd_text=jd_text,
            candidate_questions=available,
            history_turns=history_turns,
            follow_up_hint=follow_up_hint,
            turn_index=turn_index,
        )
    except Exception:
        return _fallback_interview_question(available, turn_index=turn_index)

    allowed_question_types = {
        "project_or_system_design",
        "backend_foundation",
        "coding",
        "behavioral",
        "general",
    }
    picked_id = str(picked.get("question_id") or "").strip()
    picked_text = str(picked.get("question_text") or "").strip()
    picked_mode = str(picked.get("mode") or "new_question").strip().lower()
    picked_reason = str(picked.get("reason") or "").strip()
    picked_question_type = str(picked.get("question_type") or "").strip()
    picked_reference_id = str(picked.get("reference_question_id") or "").strip()
    if picked_question_type not in allowed_question_types:
        picked_question_type = ""

    for item in available:
        if picked_id and item.get("question_id") == picked_id:
            selected = dict(item)
            if picked_text:
                # Allow the interviewer agent to rewrite retrieved questions instead of copying verbatim.
                selected["question_text"] = picked_text
            if picked_question_type:
                selected["question_type"] = picked_question_type
            selected["ask_mode"] = picked_mode or "new_question"
            selected["reason"] = picked_reason
            if picked_reference_id:
                selected["reference_question_id"] = picked_reference_id
            return selected
    for item in available:
        if picked_text and item.get("question_text") == picked_text:
            selected = dict(item)
            if picked_question_type:
                selected["question_type"] = picked_question_type
            selected["ask_mode"] = picked_mode or "new_question"
            selected["reason"] = picked_reason
            if picked_reference_id:
                selected["reference_question_id"] = picked_reference_id
            return selected

    for item in available:
        if picked_reference_id and item.get("question_id") == picked_reference_id:
            selected = dict(item)
            selected["question_id"] = picked_id or f"generated::reference::{turn_index}"
            if picked_text:
                selected["question_text"] = picked_text
            if picked_question_type:
                selected["question_type"] = picked_question_type
            selected["ask_mode"] = picked_mode if picked_mode in {"new_question", "follow_up"} else "new_question"
            selected["reason"] = picked_reason or "generated_from_reference_question"
            selected["reference_question_id"] = picked_reference_id
            return selected

    if picked_text:
        inferred_type = picked_question_type or (
            "project_or_system_design" if int(turn_index) <= 2 else "general"
        )
        return {
            "question_id": f"generated::followup::{turn_index}",
            "source_content_id": "",
            "company": target_company,
            "role": target_role,
            "section": None,
            "publish_time": None,
            "question_text": picked_text,
            "question_type": inferred_type,
            "ask_mode": picked_mode if picked_mode in {"new_question", "follow_up"} else "follow_up",
            "reason": picked_reason or "generated_by_interviewer_agent",
            "reference_question_id": picked_reference_id or None,
        }

    return _fallback_interview_question(available, turn_index=turn_index)


def _serialize_interview_turn(turn: InterviewTurn) -> dict[str, Any]:
    return {
        "turn_index": int(turn.turn_index),
        "question": turn.question_json or {},
        "answer_text": turn.answer_text or "",
        "evaluation": turn.evaluation_json or None,
        "created_at": turn.created_at.isoformat() if turn.created_at else None,
    }


def _fallback_interview_summary(turns: list[InterviewTurn]) -> dict[str, Any]:
    if not turns:
        return {
            "overall_score": 0.0,
            "dimension_scores": {
                "accuracy": 0.0,
                "depth": 0.0,
                "structure": 0.0,
                "resume_fit": 0.0,
            },
            "strengths": [],
            "improvements": ["先完成至少一轮问答再生成总结。"],
            "summary": "当前暂无可评估的面试回答。",
        }

    keys = ("accuracy", "depth", "structure", "resume_fit", "overall")
    values: dict[str, list[float]] = {key: [] for key in keys}
    for turn in turns:
        evaluation = turn.evaluation_json or {}
        scores = evaluation.get("scores") or {}
        for key in keys:
            raw = scores.get(key)
            if isinstance(raw, (int, float)):
                values[key].append(float(raw))

    def avg(key: str) -> float:
        items = values.get(key) or []
        if not items:
            return 0.0
        return round(sum(items) / len(items), 2)

    overall = avg("overall")
    return {
        "overall_score": overall,
        "dimension_scores": {
            "accuracy": avg("accuracy"),
            "depth": avg("depth"),
            "structure": avg("structure"),
            "resume_fit": avg("resume_fit"),
        },
        "strengths": [],
        "improvements": ["可补充更多技术细节与权衡过程，提高回答深度。"],
        "summary": f"共完成 {len(turns)} 轮，当前综合得分 {overall}。",
    }


def _build_interview_summary(session: InterviewSession, turns: list[InterviewTurn]) -> dict[str, Any]:
    serialized_turns = [_serialize_interview_turn(item) for item in turns]
    try:
        return agents.evaluator_agent_build_summary(
            turns=serialized_turns,
            target_company=str(session.target_company or ""),
            target_role=str(session.target_role or ""),
        )
    except Exception:
        return _fallback_interview_summary(turns)


def _finalize_interview_session(
    *,
    session: InterviewSession,
    turns: list[InterviewTurn],
    db: Session,
) -> dict[str, Any]:
    summary = session.summary_json
    if not summary:
        summary = _build_interview_summary(session, turns)
    session.status = "done"
    session.summary_json = summary
    session.current_question_json = None
    db.add(session)
    db.commit()
    return summary


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

@app.post("/api/v1/interview/retrieve", response_model=InterviewRetrieveResponse)
async def retrieve_interview_questions(req: InterviewRetrieveRequest):
    if not (req.query.strip() or req.resume_text.strip() or req.jd_text.strip()):
        raise HTTPException(
            status_code=400,
            detail="At least one of query/resume_text/jd_text is required.",
        )

    try:
        backend = _normalize_retriever_backend(req.backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    top_k = max(1, min(int(req.top_k), 20))
    try:
        retriever = _get_retriever(backend)
        results = retriever.search(
            resume_text=req.resume_text,
            jd_text=req.jd_text,
            top_k=top_k,
            extra_query=req.query or None,
            target_company=(req.target_company or "").strip() or None,
            target_role=(req.target_role or "").strip() or None,
            strict_metadata_filter=req.strict_metadata_filter,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"Retriever dataset not found: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Retriever '{backend}' failed: {exc}") from exc

    serialized_results = [serialize_retrieved_question(item) for item in results]
    return InterviewRetrieveResponse(
        backend=backend,
        top_k=top_k,
        result_count=len(serialized_results),
        results=serialized_results,
    )


@app.post("/api/v1/interview/sessions/start", response_model=InterviewSessionStartResponse)
async def start_interview_session(
    req: InterviewSessionStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        backend = _normalize_retriever_backend(req.backend)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resume_document = _load_ready_resume_document(
        db,
        user_id=current_user.id,
        resume_id=int(req.resume_id),
    )
    jd_document = _load_ready_jd_document(
        db,
        user_id=current_user.id,
        jd_id=int(req.jd_id),
    )
    resume_text = str(resume_document.source_text or "")
    jd_text = str(jd_document.source_text or "")
    target_company, target_role = _resolve_session_targets(
        current_user=current_user,
        jd_document=jd_document,
    )
    query_text = _compose_interview_query(
        target_company=target_company,
        target_role=target_role,
        jd_title=str(jd_document.title or ""),
    )

    top_k = INTERVIEW_TOP_K
    max_rounds = INTERVIEW_MAX_ROUNDS

    try:
        retriever = _get_retriever(backend)
        results = retriever.search(
            resume_text=resume_text,
            jd_text=jd_text,
            top_k=top_k,
            extra_query=query_text or None,
            target_company=target_company or None,
            target_role=target_role or None,
            strict_metadata_filter=req.strict_metadata_filter,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Interview retriever failed: {exc}") from exc

    candidate_questions = [serialize_retrieved_question(item) for item in results]
    first_question = _pick_interviewer_question(
        query=query_text,
        target_company=target_company,
        target_role=target_role,
        resume_text=resume_text,
        jd_text=jd_text,
        candidate_questions=candidate_questions,
        history_turns=[],
        follow_up_hint=None,
        turn_index=1,
    )

    session = InterviewSession(
        session_id=uuid.uuid4().hex,
        user_id=current_user.id,
        status="asking",
        backend=backend,
        top_k=top_k,
        max_rounds=max_rounds,
        current_round=1,
        query=query_text,
        resume_text=resume_text,
        jd_text=jd_text,
        target_company=target_company or None,
        target_role=target_role or None,
        current_question_json=first_question,
        summary_json=None,
    )
    turn = InterviewTurn(
        session_id=session.session_id,
        turn_index=1,
        question_json=first_question,
        answer_text=None,
        evaluation_json=None,
    )
    db.add(session)
    db.add(turn)
    db.commit()

    return InterviewSessionStartResponse(
        session_id=session.session_id,
        status=session.status,
        backend=session.backend,
        current_round=int(session.current_round),
        max_rounds=int(session.max_rounds),
        question=first_question,
    )


@app.post("/api/v1/interview/sessions/{session_id}/answer", response_model=InterviewAnswerResponse)
async def answer_interview_session(
    session_id: str,
    req: InterviewAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(InterviewSession)
        .filter(
            InterviewSession.session_id == session_id,
            InterviewSession.user_id == current_user.id,
        )
    )
    session = query.first()
    if session is None:
        raise HTTPException(status_code=404, detail="Interview session not found.")

    if session.status == "done":
        return InterviewAnswerResponse(
            session_id=session.session_id,
            status=session.status,
            current_round=int(session.current_round),
            max_rounds=int(session.max_rounds),
            evaluation={"message": "Session already finished."},
            summary=session.summary_json or {},
            next_question=None,
        )

    current_question = session.current_question_json or {}
    current_question_text = str(current_question.get("question_text") or "").strip()
    if not current_question_text:
        raise HTTPException(status_code=409, detail="Current interview question is missing.")

    turn = (
        db.query(InterviewTurn)
        .filter(
            InterviewTurn.session_id == session.session_id,
            InterviewTurn.turn_index == session.current_round,
        )
        .order_by(InterviewTurn.id.desc())
        .first()
    )
    if turn is None:
        turn = InterviewTurn(
            session_id=session.session_id,
            turn_index=int(session.current_round),
            question_json=current_question,
        )

    answer_text = req.answer_text.strip()
    turn.answer_text = answer_text

    try:
        evaluation = agents.evaluator_agent_evaluate_answer(
            question_text=current_question_text,
            answer_text=answer_text,
            resume_text=str(session.resume_text or ""),
            jd_text=str(session.jd_text or ""),
            turn_index=int(session.current_round),
            max_rounds=int(session.max_rounds),
            target_company=str(session.target_company or ""),
            target_role=str(session.target_role or ""),
        )
    except Exception:
        evaluation = {
            "scores": {
                "accuracy": 60.0,
                "depth": 60.0,
                "structure": 60.0,
                "resume_fit": 60.0,
                "overall": 60.0,
            },
            "strengths": [],
            "improvements": ["补充关键技术细节和设计取舍。"],
            "feedback": "本轮回答已记录，建议补充更多技术细节。",
            "decision": "next_question",
            "follow_up_hint": "",
        }

    decision = str(evaluation.get("decision") or "next_question").strip().lower()
    if decision not in {"follow_up", "next_question", "finish"}:
        decision = "next_question"
    if int(session.max_rounds or 0) > 0 and int(session.current_round) >= int(session.max_rounds):
        decision = "finish"

    turn.evaluation_json = evaluation
    db.add(turn)

    if decision == "finish":
        turns = (
            db.query(InterviewTurn)
            .filter(InterviewTurn.session_id == session.session_id)
            .order_by(InterviewTurn.turn_index.asc(), InterviewTurn.id.asc())
            .all()
        )
        summary = _finalize_interview_session(session=session, turns=turns, db=db)
        return InterviewAnswerResponse(
            session_id=session.session_id,
            status=session.status,
            current_round=int(session.current_round),
            max_rounds=int(session.max_rounds),
            evaluation=evaluation,
            summary=summary,
            next_question=None,
        )

    history_turns = (
        db.query(InterviewTurn)
        .filter(InterviewTurn.session_id == session.session_id)
        .order_by(InterviewTurn.turn_index.asc(), InterviewTurn.id.asc())
        .all()
    )
    serialized_history = [_serialize_interview_turn(item) for item in history_turns]

    candidate_questions: list[dict[str, Any]] = []
    try:
        retriever = _get_retriever(str(session.backend or "v2"))
        next_results = retriever.search(
            resume_text=str(session.resume_text or ""),
            jd_text=str(session.jd_text or ""),
            top_k=int(session.top_k or 8),
            extra_query=str(session.query or "") or None,
            target_company=str(session.target_company or "") or None,
            target_role=str(session.target_role or "") or None,
            strict_metadata_filter=False,
        )
        candidate_questions = [serialize_retrieved_question(item) for item in next_results]
    except Exception:
        candidate_questions = []

    next_round = int(session.current_round) + 1
    next_question = _pick_interviewer_question(
        query=str(session.query or ""),
        target_company=str(session.target_company or ""),
        target_role=str(session.target_role or ""),
        resume_text=str(session.resume_text or ""),
        jd_text=str(session.jd_text or ""),
        candidate_questions=candidate_questions,
        history_turns=serialized_history,
        follow_up_hint=str(evaluation.get("follow_up_hint") or "") if decision == "follow_up" else None,
        turn_index=next_round,
    )

    next_turn = InterviewTurn(
        session_id=session.session_id,
        turn_index=next_round,
        question_json=next_question,
        answer_text=None,
        evaluation_json=None,
    )
    session.current_round = next_round
    session.current_question_json = next_question
    session.status = "asking"
    db.add(next_turn)
    db.add(session)
    db.commit()

    return InterviewAnswerResponse(
        session_id=session.session_id,
        status=session.status,
        current_round=int(session.current_round),
        max_rounds=int(session.max_rounds),
        evaluation=evaluation,
        summary=None,
        next_question=next_question,
    )


@app.get("/api/v1/interview/sessions/{session_id}/summary", response_model=InterviewSummaryResponse)
async def get_interview_session_summary(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(InterviewSession)
        .filter(
            InterviewSession.session_id == session_id,
            InterviewSession.user_id == current_user.id,
        )
    )
    session = query.first()
    if session is None:
        raise HTTPException(status_code=404, detail="Interview session not found.")

    turns = (
        db.query(InterviewTurn)
        .filter(InterviewTurn.session_id == session.session_id)
        .order_by(InterviewTurn.turn_index.asc(), InterviewTurn.id.asc())
        .all()
    )
    if not session.summary_json:
        session.summary_json = _build_interview_summary(session, turns)
        db.add(session)
        db.commit()

    return InterviewSummaryResponse(
        session_id=session.session_id,
        status=session.status,
        current_round=int(session.current_round),
        max_rounds=int(session.max_rounds),
        summary=session.summary_json or _fallback_interview_summary(turns),
        turns=[_serialize_interview_turn(item) for item in turns],
    )


@app.post("/api/v1/interview/sessions/{session_id}/finish", response_model=InterviewSummaryResponse)
async def finish_interview_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(InterviewSession)
        .filter(
            InterviewSession.session_id == session_id,
            InterviewSession.user_id == current_user.id,
        )
    )
    session = query.first()
    if session is None:
        raise HTTPException(status_code=404, detail="Interview session not found.")

    turns = (
        db.query(InterviewTurn)
        .filter(InterviewTurn.session_id == session.session_id)
        .order_by(InterviewTurn.turn_index.asc(), InterviewTurn.id.asc())
        .all()
    )
    summary = _finalize_interview_session(session=session, turns=turns, db=db)

    return InterviewSummaryResponse(
        session_id=session.session_id,
        status=session.status,
        current_round=int(session.current_round),
        max_rounds=int(session.max_rounds),
        summary=summary or _fallback_interview_summary(turns),
        turns=[_serialize_interview_turn(item) for item in turns],
    )


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
async def chat_with_agent(req: ChatRequest):
    if not _feature_enabled("ENABLE_CHAT", True):
        raise HTTPException(status_code=404, detail="Chat feature is disabled")

    try:
        llm = ChatOpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
            model="deepseek-chat",
            temperature=0.2,
        )
        prompt = (
            "You are JobCopilot, a practical career assistant. "
            "Give concise, actionable, and honest guidance in Chinese.\n"
            f"User message: {req.message}"
        )
        reply = await llm.ainvoke(prompt)
        return {"reply": (reply.content or "").strip()}
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))
