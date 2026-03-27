from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


DocumentStatus = Literal["processing", "ready", "error"]


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ValueError("value cannot be empty")
    return text


class ResumeDocumentSummaryResponse(BaseModel):
    id: int
    title: str
    source_filename: str | None = None
    status: DocumentStatus
    error: str | None = None
    is_active: bool
    char_count: int
    created_at: datetime
    updated_at: datetime


class ResumeDocumentDetailResponse(ResumeDocumentSummaryResponse):
    source_text: str


class JDDocumentSummaryResponse(BaseModel):
    id: int
    title: str
    status: DocumentStatus
    error: str | None = None
    is_active: bool
    char_count: int
    created_at: datetime
    updated_at: datetime


class JDDocumentDetailResponse(JDDocumentSummaryResponse):
    source_text: str


class ResumeDocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    source_text: str | None = None
    is_active: bool | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)


class JDDocumentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    source_text: str = Field(min_length=1)
    is_active: bool = False

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = _normalize_optional_text(value)
        assert normalized is not None
        return normalized

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        normalized = _normalize_optional_text(value)
        assert normalized is not None
        return normalized


class JDDocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    source_text: str | None = None
    is_active: bool | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)
