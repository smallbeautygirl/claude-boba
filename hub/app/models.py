"""資料模型。SPEC.md §6。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import DebtStatus, DebtTier, JobStatus, SourceType


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _enum(enum_cls: type) -> SAEnum:
    """以字串值存進 DB，讀回來是 enum 實例。

    直接用 String 欄位配 Mapped[SomeEnum] 是行不通的 —— SQLAlchemy 不會轉型，
    讀回來會是純 str，而 str 沒有 JobStatus.creates_debt 這種屬性。
    native_enum=False 是為了避免在 Postgres 建立原生 enum 型別（改值要跑 migration）。
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=16,
        values_callable=lambda e: [m.value for m in e],
    )


class User(Base):
    """由 Observ 提供身分。我們不管密碼，只記 Observ 的 user id 與 email。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    observ_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    # worker 的身分是 Hub 發的 token，不是出租者的 Observ 帳密 ——
    # 把公司密碼寫進 worker 的 .env 是不必要的風險。出租者在網頁按「產生 token」，
    # 貼進 .env 就好，worker 完全不碰 Observ。
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    name: Mapped[str] = mapped_column(String(80))
    token: Mapped[str] = mapped_column(String(128), unique=True)

    owner: Mapped[User] = relationship()

    online: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepting: Mapped[bool] = mapped_column(Boolean, default=True)

    # 出租者的設定，worker 註冊時回報
    allow_full_network: Mapped[bool] = mapped_column(Boolean, default=False)
    available_models: Mapped[list[str]] = mapped_column(JSONB, default=list)
    job_budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(5))
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    claude_code_version: Mapped[str | None] = mapped_column(String(32))

    # rate_limit_event 回報的額度使用率（SPEC §4.6）。
    # 只顯示紅綠燈，不對外顯示精確百分比（docs/web-spec.md §3）。
    utilization_five_hour: Mapped[float | None] = mapped_column()
    utilization_seven_day: Mapped[float | None] = mapped_column()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus), default=JobStatus.QUEUED, index=True
    )

    borrower_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    borrower: Mapped[User] = relationship(foreign_keys=[borrower_id])

    source_type: Mapped[SourceType] = mapped_column(_enum(SourceType))
    prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64), default="sonnet")

    # 指定出租者；None 代表自動派單
    requested_worker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workers.id")
    )
    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workers.id")
    )

    # 續問：指向被接續的那個 job，以及它留下的 transcript。
    # 為了支援續問，job 容器不再用 --no-session-persistence（SPEC.md §4.2）。
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id")
    )
    transcript_key: Mapped[str | None] = mapped_column(String(512))
    output_prefix: Mapped[str | None] = mapped_column(String(512))

    # 版本閘門是「記錄 + 警告」，不擋下（SPEC §11 spike 3）
    borrower_cli_version: Mapped[str | None] = mapped_column(String(32))
    lender_cli_version: Mapped[str | None] = mapped_column(String(32))

    error_kind: Mapped[str | None] = mapped_column(String(48))
    error_detail: Mapped[str | None] = mapped_column(Text)
    # 出租者按停止時可選填的一句話。沒有它，停止會被讀成拒絕（web-spec §8）。
    stop_note: Mapped[str | None] = mapped_column(Text)

    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    result_text: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    worker: Mapped[Worker | None] = relationship(foreign_keys=[worker_id])
    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job", order_by="JobEvent.seq"
    )
    usages: Mapped[list[Usage]] = relationship(back_populates="job")


class JobEvent(Base):
    """stream-json 的事件流。

    保存整份是必要的，不是可有可無：使用者可以關掉分頁再回來，屆時要先重播歷史
    再接上即時串流（docs/web-spec.md §4）。seq 同時用於 worker 重送去重與
    瀏覽器 SSE 斷線續傳。
    """

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_seq", "job_id", "seq", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped[Job] = relationship(back_populates="events")


class Usage(Base):
    """每個 model 的用量與成本。

    金額直接採信 CLI 回報的 costUSD（SPEC §7）—— 它的 costBasis 是 "list"，
    也就是 API 標價，正是 §4.6 要的「API 等價金額」。我們不自己算費率。
    """

    __tablename__ = "usages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE")
    )

    model: Mapped[str] = mapped_column(String(64))
    canonical_model: Mapped[str | None] = mapped_column(String(64))
    cost_basis: Mapped[str | None] = mapped_column(String(16))

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)

    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))

    job: Mapped[Job] = relationship(back_populates="usages")


class Debt(Base):
    """一筆人情債。

    只有 succeeded 的 job 會產生（SPEC.md §5）。金額是 API 等價金額，
    級距換算見 pricing.py —— 級距刻意做得很粗，精確數字會讓人開始計較。

    不設到期日，但顯示欠了幾天：比自動勾銷更有社交壓力，也更好笑（SPEC.md §4.8）。
    """

    __tablename__ = "debts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), unique=True
    )
    borrower_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    lender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )

    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    tier: Mapped[DebtTier] = mapped_column(_enum(DebtTier))
    status: Mapped[DebtStatus] = mapped_column(
        _enum(DebtStatus), default=DebtStatus.OPEN
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship()
    borrower: Mapped[User] = relationship(foreign_keys=[borrower_id])
    lender: Mapped[User] = relationship(foreign_keys=[lender_id])


class Artifact(Base):
    """job 產出的檔案。

    檔案本身在 MinIO，這裡只記 metadata。下載一律走短效期的預簽 URL
    （.claude/rules/security.md）。
    """

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    key: Mapped[str] = mapped_column(String(768))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    job: Mapped[Job] = relationship()
