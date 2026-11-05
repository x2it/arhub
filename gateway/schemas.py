"""统一响应契约：四路检索一律同一形状，消除原型 count:None / rrf_score 不一致。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class SearchHit(BaseModel):
    doc_id: int
    title: Optional[str] = None
    asn: Optional[int] = None
    created: Optional[str] = None
    score: Optional[float] = None
    rrf_score: Optional[float] = None
    matched_by: list[str] = Field(default_factory=list)
    source: str = "hybrid"
    chunk_index: Optional[int] = None
    snippet: Optional[str] = None
    highlights: Optional[str] = None
    classification: Optional[int] = None
    # 浏览模式（空检索词）专用：标注 OCR 是否为空，避免「有档案却查不到内容」的困惑
    ocr_empty: Optional[bool] = None
    content_length: Optional[int] = None


class SearchResponse(BaseModel):
    request_id: str
    mode: str
    total: int
    degraded: bool = False
    browsing: bool = False
    results: list[SearchHit]


class UploadResponse(BaseModel):
    status: str
    doc_id: Optional[int] = None
    duplicate: bool = False
    task_enqueued: bool = False
    message: str
    request_id: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]


class TaskAccepted(BaseModel):
    status: str = "accepted"
    task_id: Optional[int] = None
    request_id: str
