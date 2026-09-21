from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from .enums import JobStatus, SourceType

# 上傳限制。SPEC.md §11：一句 "pong" 的 transcript 就 224 KB，真實 RD session
# 估 10–50 MB。超過 50 MB 的 session，--resume 本身也會慢到不實用。
MAX_PROMPT_CHARS = 2_000_000


class JobCreate(BaseModel):
    # 借用者身分由 Authorization header 決定，不接受從 body 指定 ——
    # 否則任何人都能用別人的名義掛債。
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str = Field(default="sonnet", max_length=64)
    source_type: SourceType = SourceType.PASTE
    requested_worker_id: uuid.UUID | None = None
    borrower_cli_version: str | None = Field(default=None, max_length=32)


class JobSummary(BaseModel):
    id: uuid.UUID
    status: JobStatus
    borrower: str
    model: str
    created_at: datetime
    finished_at: datetime | None
    total_cost_usd: Decimal | None


class JobDetail(JobSummary):
    lender: str | None
    prompt: str
    source_type: SourceType
    worker_id: uuid.UUID | None
    result_text: str | None
    error_kind: str | None
    error_detail: str | None
    stop_note: str | None
    lender_cli_version: str | None
    borrower_cli_version: str | None
    debt_label: str | None


class WorkerConfig(BaseModel):
    """worker 啟動時回報自己的設定。身分已由 token 決定，所以沒有 name。"""

    allow_full_network: bool = False
    available_models: list[str] = Field(default_factory=lambda: ["sonnet", "haiku"])
    job_budget_usd: Decimal = Decimal(5)
    max_concurrency: int = 1
    claude_code_version: str | None = None


class WorkerJob(BaseModel):
    """Hub 派給 worker 的工作。"""

    job_id: uuid.UUID
    prompt: str
    model: str
    source_type: SourceType
    transcript_key: str | None
    job_budget_usd: Decimal
    available_models: list[str]


class EventBatch(BaseModel):
    """worker 每 ~500ms 批次回報一次。seq 用於去重與續傳。"""

    from_seq: int = Field(ge=0)
    events: list[dict[str, Any]]


class JobResult(BaseModel):
    """worker 回報最終結果。欄位對應 stream-json 的 result 事件。"""

    status: JobStatus
    result_text: str | None = None
    total_cost_usd: Decimal | None = None
    model_usage: dict[str, dict[str, Any]] = Field(default_factory=dict)
    error_kind: str | None = None
    error_detail: str | None = None
    lender_cli_version: str | None = None
