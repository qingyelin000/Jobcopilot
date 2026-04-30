from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    location_consent = Column(Boolean, nullable=False, default=False)
    full_name = Column(String(120), nullable=True)
    email = Column(String(120), nullable=True)
    phone = Column(String(40), nullable=True)
    city = Column(String(80), nullable=True)
    target_role = Column(String(120), nullable=True)
    profile_summary = Column(Text, nullable=True)
    plan = Column(String(20), nullable=True, default="free")
    llm_processing_consent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResumeProcessJob(Base):
    __tablename__ = "resume_process_jobs"

    job_id = Column(String(32), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    cache_key = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, index=True)
    stage = Column(String(16), nullable=False, index=True)
    progress = Column(Integer, nullable=False, default=0)
    message = Column(String(255), nullable=False)
    data = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResumeDocument(Base):
    __tablename__ = "resume_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(120), nullable=False)
    source_filename = Column(String(255), nullable=True)
    source_text = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, index=True, default="processing")
    parsed_json = Column(JSON, nullable=True)
    interview_profile_json = Column(JSON, nullable=True)
    parser_model = Column(String(64), nullable=True)
    schema_version = Column(String(32), nullable=False, default="v1")
    error = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JDDocument(Base):
    __tablename__ = "jd_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(120), nullable=False)
    source_text = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, index=True, default="processing")
    parsed_json = Column(JSON, nullable=True)
    interview_profile_json = Column(JSON, nullable=True)
    parser_model = Column(String(64), nullable=True)
    schema_version = Column(String(32), nullable=False, default="v1")
    error = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    session_id = Column(String(32), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    status = Column(String(16), nullable=False, index=True, default="asking")
    backend = Column(String(8), nullable=False, default="v2")
    top_k = Column(Integer, nullable=False, default=8)
    max_rounds = Column(Integer, nullable=False, default=5)
    current_round = Column(Integer, nullable=False, default=1)
    query = Column(Text, nullable=True)
    resume_text = Column(Text, nullable=False)
    jd_text = Column(Text, nullable=False)
    retrieval_resume_text = Column(Text, nullable=True)
    retrieval_jd_text = Column(Text, nullable=True)
    target_company = Column(String(120), nullable=True)
    target_role = Column(String(120), nullable=True)
    current_question_json = Column(JSON, nullable=True)
    summary_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class InterviewTurn(Base):
    __tablename__ = "interview_turns"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(32), ForeignKey("interview_sessions.session_id"), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False, index=True)
    question_json = Column(JSON, nullable=False)
    answer_text = Column(Text, nullable=True)
    evaluation_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


# === Batch E/F/G/H/I 业务模型扩展 ===


class ApplicationRecord(Base):
    __tablename__ = "application_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company = Column(String(160), nullable=False)
    role = Column(String(160), nullable=False)
    jd_document_id = Column(Integer, ForeignKey("jd_documents.id"), nullable=True)
    resume_document_id = Column(Integer, ForeignKey("resume_documents.id"), nullable=True)
    process_job_id = Column(String(64), nullable=True, index=True)
    channel = Column(String(80), nullable=True)
    stage = Column(String(40), nullable=False, default="planned")
    deadline_at = Column(DateTime, nullable=True)
    last_contact_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    extra_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ApplicationTimeline(Base):
    __tablename__ = "application_timelines"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("application_records.id"), nullable=False, index=True)
    from_stage = Column(String(40), nullable=True)
    to_stage = Column(String(40), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(200), nullable=True)
    profile_snapshot_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    actor = Column(String(80), nullable=True)
    action = Column(String(80), nullable=False, index=True)
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(64), nullable=True, index=True)
    ip = Column(String(64), nullable=True)
    ua = Column(String(255), nullable=True)
    payload_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    route = Column(String(120), nullable=False, index=True)
    model = Column(String(80), nullable=True)
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    cost_estimate = Column(String(40), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class FeedbackEntry(Base):
    __tablename__ = "feedback_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    target_type = Column(String(40), nullable=False)
    target_id = Column(String(64), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event = Column(String(80), nullable=False, index=True)
    properties_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
