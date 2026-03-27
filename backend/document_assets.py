import asyncio
import hashlib
import os
from io import BytesIO
from pathlib import Path
from typing import Any

import agents
from auth import decode_access_token
from db import SessionLocal, get_db
from document_schemas import (
    JDDocumentCreateRequest,
    JDDocumentDetailResponse,
    JDDocumentSummaryResponse,
    JDDocumentUpdateRequest,
    ResumeDocumentDetailResponse,
    ResumeDocumentSummaryResponse,
    ResumeDocumentUpdateRequest,
)
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from models import JDDocument, ResumeDocument, User
from PyPDF2 import PdfReader
from sqlalchemy.orm import Session


router = APIRouter(tags=["documents"])

MAX_RESUME_DOCUMENTS = 3
MAX_JD_DOCUMENTS = 3
DOCUMENT_SCHEMA_VERSION = "v1"


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix):].strip()
    return None


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")

    username = decode_access_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="登录状态已失效")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def _extract_text_from_pdf_bytes(file_bytes: bytes) -> tuple[str, int]:
    reader = PdfReader(BytesIO(file_bytes))
    pages: list[str] = []

    for page in reader.pages:
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(page_text)

    return "\n\n".join(pages).strip(), len(reader.pages)


def _build_content_hash(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def _normalize_title(title: str | None, fallback: str) -> str:
    value = (title or "").strip()
    if value:
        return value[:120]

    normalized_fallback = fallback.strip()[:120]
    return normalized_fallback or "未命名文档"


def _serialize_resume_document(
    document: ResumeDocument,
    *,
    include_source_text: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": document.id,
        "title": document.title,
        "source_filename": document.source_filename,
        "status": document.status,
        "error": document.error,
        "is_active": document.is_active,
        "char_count": len(document.source_text or ""),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if include_source_text:
        payload["source_text"] = document.source_text
    return payload


def _serialize_jd_document(
    document: JDDocument,
    *,
    include_source_text: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": document.id,
        "title": document.title,
        "status": document.status,
        "error": document.error,
        "is_active": document.is_active,
        "char_count": len(document.source_text or ""),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if include_source_text:
        payload["source_text"] = document.source_text
    return payload


def _has_active_document(db: Session, model: Any, user_id: int) -> bool:
    return (
        db.query(model)
        .filter(model.user_id == user_id, model.is_active.is_(True))
        .first()
        is not None
    )


def _set_only_active_document(db: Session, model: Any, user_id: int, document_id: int) -> None:
    documents = db.query(model).filter(model.user_id == user_id).all()
    for document in documents:
        document.is_active = document.id == document_id
        db.add(document)


def _assign_fallback_active_document(db: Session, model: Any, user_id: int) -> None:
    active_exists = _has_active_document(db, model, user_id)
    if active_exists:
        return

    latest_document = (
        db.query(model)
        .filter(model.user_id == user_id)
        .order_by(model.updated_at.desc(), model.id.desc())
        .first()
    )
    if latest_document is None:
        return

    latest_document.is_active = True
    db.add(latest_document)


def _mark_documents_as_error(model: Any) -> None:
    db = SessionLocal()
    try:
        processing_documents = db.query(model).filter(model.status == "processing").all()
        for document in processing_documents:
            document.status = "error"
            document.error = "server_restarted"
            db.add(document)
        if processing_documents:
            db.commit()
    finally:
        db.close()


def mark_interrupted_document_jobs() -> None:
    _mark_documents_as_error(ResumeDocument)
    _mark_documents_as_error(JDDocument)


async def _process_resume_document(document_id: int, expected_hash: str) -> None:
    source_text = ""
    db = SessionLocal()
    try:
        document = db.query(ResumeDocument).filter(ResumeDocument.id == document_id).first()
        if document is None or document.content_hash != expected_hash:
            return
        source_text = document.source_text
    finally:
        db.close()

    try:
        parsed = await asyncio.to_thread(agents.parse_resume_to_json, source_text)
        profile = agents.build_resume_interview_profile(parsed)
    except Exception as exc:
        db = SessionLocal()
        try:
            document = db.query(ResumeDocument).filter(ResumeDocument.id == document_id).first()
            if document is None or document.content_hash != expected_hash:
                return
            document.status = "error"
            document.error = str(exc)[:2000]
            document.parsed_json = None
            document.interview_profile_json = None
            db.add(document)
            db.commit()
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        document = db.query(ResumeDocument).filter(ResumeDocument.id == document_id).first()
        if document is None or document.content_hash != expected_hash:
            return
        document.status = "ready"
        document.error = None
        document.parsed_json = parsed.model_dump()
        document.interview_profile_json = profile.model_dump()
        document.parser_model = os.environ.get("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
        document.schema_version = DOCUMENT_SCHEMA_VERSION
        db.add(document)
        db.commit()
    finally:
        db.close()


async def _process_jd_document(document_id: int, expected_hash: str) -> None:
    source_text = ""
    db = SessionLocal()
    try:
        document = db.query(JDDocument).filter(JDDocument.id == document_id).first()
        if document is None or document.content_hash != expected_hash:
            return
        source_text = document.source_text
    finally:
        db.close()

    try:
        parsed = await asyncio.to_thread(agents.parse_jd_to_json, source_text)
        profile = agents.build_jd_interview_profile(parsed)
    except Exception as exc:
        db = SessionLocal()
        try:
            document = db.query(JDDocument).filter(JDDocument.id == document_id).first()
            if document is None or document.content_hash != expected_hash:
                return
            document.status = "error"
            document.error = str(exc)[:2000]
            document.parsed_json = None
            document.interview_profile_json = None
            db.add(document)
            db.commit()
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        document = db.query(JDDocument).filter(JDDocument.id == document_id).first()
        if document is None or document.content_hash != expected_hash:
            return
        document.status = "ready"
        document.error = None
        document.parsed_json = parsed.model_dump()
        document.interview_profile_json = profile.model_dump()
        document.parser_model = os.environ.get("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
        document.schema_version = DOCUMENT_SCHEMA_VERSION
        db.add(document)
        db.commit()
    finally:
        db.close()


def _requeue_resume_document(document: ResumeDocument) -> None:
    asyncio.create_task(_process_resume_document(document.id, document.content_hash))


def _requeue_jd_document(document: JDDocument) -> None:
    asyncio.create_task(_process_jd_document(document.id, document.content_hash))


@router.post("/api/v1/resumes/upload-pdf", response_model=ResumeDocumentSummaryResponse)
async def upload_resume_pdf(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 简历")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传的 PDF 为空")

    try:
        source_text, _page_count = await asyncio.to_thread(_extract_text_from_pdf_bytes, file_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 解析失败: {exc}") from exc

    if not source_text:
        raise HTTPException(status_code=400, detail="PDF 中没有提取到可用文本")

    content_hash = _build_content_hash(source_text)
    existing_document = (
        db.query(ResumeDocument)
        .filter(
            ResumeDocument.user_id == current_user.id,
            ResumeDocument.content_hash == content_hash,
        )
        .first()
    )
    if existing_document is not None:
        normalized_title = _normalize_title(title, Path(filename).stem or "简历")
        if normalized_title and existing_document.title != normalized_title:
            existing_document.title = normalized_title
            db.add(existing_document)
            db.commit()
            db.refresh(existing_document)
        return _serialize_resume_document(existing_document)

    resume_count = db.query(ResumeDocument).filter(ResumeDocument.user_id == current_user.id).count()
    if resume_count >= MAX_RESUME_DOCUMENTS:
        raise HTTPException(status_code=400, detail="最多只能保存 3 份简历")

    should_activate = not _has_active_document(db, ResumeDocument, current_user.id)
    if should_activate:
        _set_only_active_document(db, ResumeDocument, current_user.id, -1)

    document = ResumeDocument(
        user_id=current_user.id,
        title=_normalize_title(title, Path(filename).stem or "简历"),
        source_filename=filename or None,
        source_text=source_text,
        content_hash=content_hash,
        status="processing",
        parsed_json=None,
        interview_profile_json=None,
        parser_model=None,
        schema_version=DOCUMENT_SCHEMA_VERSION,
        error=None,
        is_active=should_activate,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    _requeue_resume_document(document)
    return _serialize_resume_document(document)


@router.get("/api/v1/resumes", response_model=list[ResumeDocumentSummaryResponse])
def list_resume_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    documents = (
        db.query(ResumeDocument)
        .filter(ResumeDocument.user_id == current_user.id)
        .order_by(ResumeDocument.is_active.desc(), ResumeDocument.updated_at.desc(), ResumeDocument.id.desc())
        .all()
    )
    return [_serialize_resume_document(document) for document in documents]


@router.get("/api/v1/resumes/{resume_id}", response_model=ResumeDocumentDetailResponse)
def get_resume_document(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(ResumeDocument)
        .filter(ResumeDocument.id == resume_id, ResumeDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="简历不存在")
    return _serialize_resume_document(document, include_source_text=True)


@router.patch("/api/v1/resumes/{resume_id}", response_model=ResumeDocumentSummaryResponse)
async def update_resume_document(
    resume_id: int,
    req: ResumeDocumentUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.title is None and req.source_text is None and req.is_active is None:
        raise HTTPException(status_code=400, detail="没有可更新的内容")

    document = (
        db.query(ResumeDocument)
        .filter(ResumeDocument.id == resume_id, ResumeDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="简历不存在")

    should_reprocess = False

    if req.title is not None:
        document.title = req.title

    if req.source_text is not None and req.source_text != document.source_text:
        duplicate = (
            db.query(ResumeDocument)
            .filter(
                ResumeDocument.user_id == current_user.id,
                ResumeDocument.content_hash == _build_content_hash(req.source_text),
                ResumeDocument.id != document.id,
            )
            .first()
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="已存在内容相同的简历")

        document.source_text = req.source_text
        document.content_hash = _build_content_hash(req.source_text)
        document.status = "processing"
        document.error = None
        document.parsed_json = None
        document.interview_profile_json = None
        document.parser_model = None
        document.schema_version = DOCUMENT_SCHEMA_VERSION
        should_reprocess = True

    if req.is_active is True:
        _set_only_active_document(db, ResumeDocument, current_user.id, document.id)
    elif req.is_active is False:
        document.is_active = False

    db.add(document)
    db.commit()
    db.refresh(document)

    if should_reprocess:
        _requeue_resume_document(document)

    return _serialize_resume_document(document)


@router.post("/api/v1/resumes/{resume_id}/reprocess", response_model=ResumeDocumentSummaryResponse)
async def reprocess_resume_document(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(ResumeDocument)
        .filter(ResumeDocument.id == resume_id, ResumeDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="简历不存在")

    document.status = "processing"
    document.error = None
    document.parsed_json = None
    document.interview_profile_json = None
    document.parser_model = None
    document.schema_version = DOCUMENT_SCHEMA_VERSION
    db.add(document)
    db.commit()
    db.refresh(document)

    _requeue_resume_document(document)
    return _serialize_resume_document(document)


@router.delete("/api/v1/resumes/{resume_id}")
def delete_resume_document(
    resume_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(ResumeDocument)
        .filter(ResumeDocument.id == resume_id, ResumeDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="简历不存在")

    was_active = document.is_active
    db.delete(document)
    db.commit()

    if was_active:
        _assign_fallback_active_document(db, ResumeDocument, current_user.id)
        db.commit()

    return {"success": True}


@router.post("/api/v1/jds", response_model=JDDocumentSummaryResponse)
async def create_jd_document(
    req: JDDocumentCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content_hash = _build_content_hash(req.source_text)
    existing_document = (
        db.query(JDDocument)
        .filter(JDDocument.user_id == current_user.id, JDDocument.content_hash == content_hash)
        .first()
    )
    if existing_document is not None:
        if existing_document.title != req.title:
            existing_document.title = req.title
        if req.is_active:
            _set_only_active_document(db, JDDocument, current_user.id, existing_document.id)
        db.add(existing_document)
        db.commit()
        db.refresh(existing_document)
        return _serialize_jd_document(existing_document)

    jd_count = db.query(JDDocument).filter(JDDocument.user_id == current_user.id).count()
    if jd_count >= MAX_JD_DOCUMENTS:
        raise HTTPException(status_code=400, detail="最多只能保存 3 份 JD")

    should_activate = req.is_active or not _has_active_document(db, JDDocument, current_user.id)
    if should_activate:
        _set_only_active_document(db, JDDocument, current_user.id, -1)

    document = JDDocument(
        user_id=current_user.id,
        title=req.title,
        source_text=req.source_text,
        content_hash=content_hash,
        status="processing",
        parsed_json=None,
        interview_profile_json=None,
        parser_model=None,
        schema_version=DOCUMENT_SCHEMA_VERSION,
        error=None,
        is_active=should_activate,
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    _requeue_jd_document(document)
    return _serialize_jd_document(document)


@router.get("/api/v1/jds", response_model=list[JDDocumentSummaryResponse])
def list_jd_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    documents = (
        db.query(JDDocument)
        .filter(JDDocument.user_id == current_user.id)
        .order_by(JDDocument.is_active.desc(), JDDocument.updated_at.desc(), JDDocument.id.desc())
        .all()
    )
    return [_serialize_jd_document(document) for document in documents]


@router.get("/api/v1/jds/{jd_id}", response_model=JDDocumentDetailResponse)
def get_jd_document(
    jd_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(JDDocument)
        .filter(JDDocument.id == jd_id, JDDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="JD 不存在")
    return _serialize_jd_document(document, include_source_text=True)


@router.patch("/api/v1/jds/{jd_id}", response_model=JDDocumentSummaryResponse)
async def update_jd_document(
    jd_id: int,
    req: JDDocumentUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.title is None and req.source_text is None and req.is_active is None:
        raise HTTPException(status_code=400, detail="没有可更新的内容")

    document = (
        db.query(JDDocument)
        .filter(JDDocument.id == jd_id, JDDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="JD 不存在")

    should_reprocess = False

    if req.title is not None:
        document.title = req.title

    if req.source_text is not None and req.source_text != document.source_text:
        duplicate_hash = _build_content_hash(req.source_text)
        duplicate = (
            db.query(JDDocument)
            .filter(
                JDDocument.user_id == current_user.id,
                JDDocument.content_hash == duplicate_hash,
                JDDocument.id != document.id,
            )
            .first()
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="已存在内容相同的 JD")

        document.source_text = req.source_text
        document.content_hash = duplicate_hash
        document.status = "processing"
        document.error = None
        document.parsed_json = None
        document.interview_profile_json = None
        document.parser_model = None
        document.schema_version = DOCUMENT_SCHEMA_VERSION
        should_reprocess = True

    if req.is_active is True:
        _set_only_active_document(db, JDDocument, current_user.id, document.id)
    elif req.is_active is False:
        document.is_active = False

    db.add(document)
    db.commit()
    db.refresh(document)

    if should_reprocess:
        _requeue_jd_document(document)

    return _serialize_jd_document(document)


@router.post("/api/v1/jds/{jd_id}/reprocess", response_model=JDDocumentSummaryResponse)
async def reprocess_jd_document(
    jd_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(JDDocument)
        .filter(JDDocument.id == jd_id, JDDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="JD 不存在")

    document.status = "processing"
    document.error = None
    document.parsed_json = None
    document.interview_profile_json = None
    document.parser_model = None
    document.schema_version = DOCUMENT_SCHEMA_VERSION
    db.add(document)
    db.commit()
    db.refresh(document)

    _requeue_jd_document(document)
    return _serialize_jd_document(document)


@router.delete("/api/v1/jds/{jd_id}")
def delete_jd_document(
    jd_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = (
        db.query(JDDocument)
        .filter(JDDocument.id == jd_id, JDDocument.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="JD 不存在")

    was_active = document.is_active
    db.delete(document)
    db.commit()

    if was_active:
        _assign_fallback_active_document(db, JDDocument, current_user.id)
        db.commit()

    return {"success": True}
