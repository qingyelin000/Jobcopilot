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
