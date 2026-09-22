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
    LargeBinary,
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
    # 每個人自己在 Teams 建的 Workflows webhook。沒設就沒有通知。
    teams_webhook_url: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkerHost(Base):
    """領單的那台主機。

    託管模型下 job 全跑在同一台機器上，而它要能用**任何一位**代跑者的 token 跑 ——
    所以 worker 的身分是「我是這台主機」，不是「我是某位代跑者」。
    憑證由 Hub 隨每個 job 派下來（`WorkerJob.oauth_token`）。

    ⚠️ 這是 2026-09-22 改的。舊協定是「一個 token 對一位代跑者」，而那讓
    「一個人兩個帳號」做不出來：帳號不是被 Hub 挑的，是自己跑來搶單的
    （SPEC §4.12）。token 現在來自 hub 的 `WORKER_SHARED_TOKEN`，不再由網頁產生。
    """

    __tablename__ = "worker_hosts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(80), default="host")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claude_code_version: Mapped[str | None] = mapped_column(String(32))
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2)


class LendingSetting(Base):
    """一位代跑者出借額度的**條件**。每人恰好一份。

    這裡的「一份」指的是一組條件，不是一個帳號 —— 他可以有多個**出借帳號**
    （`LendingAccount`），但條件只有一份：上限 US$5 講的是他對風險的態度，
    不是他對某個帳號的態度（SPEC §4.12）。
    """

    __tablename__ = "lending_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True
    )
    owner: Mapped[User] = relationship()

    allow_full_network: Mapped[bool] = mapped_column(Boolean, default=False)
    available_models: Mapped[list[str]] = mapped_column(JSONB, default=list)
    job_budget_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal(5))
    accepting: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    accounts: Mapped[list[LendingAccount]] = relationship(
        back_populates="lending", order_by="LendingAccount.created_at"
    )


class LendingAccount(Base):
    """被借出去的那個 Claude 帳號本身。一位代跑者可以有多個。

    帳號持有 token、額度、rate limit 與併發上限 —— 那些是帳號的物理性質。
    條件在 `LendingSetting` 上，因為那是人的態度。

    **對委託者不可見**（ADR-0001）：他挑的是人，Hub 自己決定用哪個帳號跑。
    """

    __tablename__ = "lending_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    lending_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lending_settings.id")
    )
    lending: Mapped[LendingSetting] = relationship(back_populates="accounts")

    name: Mapped[str] = mapped_column(String(80))

    # 代跑者的長期 OAuth token，加密後存放（app/secrets_box.py）。
    #
    # **絕不回傳給前端**，連遮罩後的值都不行 —— API 只回 has_token: bool
    # （security.md 紅線 2）。托管模型下這是 job 唯一的憑證來源。
    oauth_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    # 「這是誰的額度／誰批准的」。純文字，站台不驗證，只留痕跡並顯示在管理頁。
    #
    # 個人帳號被停權是他自己倒楣；公司帳號被停權是公司資產出事，而且會有人問
    # 是誰放的（SPEC §4.12）。不做審批流程 —— 只有個位數使用者，太重 ——
    # 但那個答案必須存在某處。
    approver_note: Mapped[str | None] = mapped_column(String(200))

    # token 失效（到期、被撤銷、帳號被收回）。**只有這個帳號停止接單，
    # 其他帳號照跑**（web-spec §8）—— job 不受影響，另一個帳號會接。
    needs_reauth: Mapped[bool] = mapped_column(Boolean, default=False)

    # 同時能跑幾個。這不是使用者在表達意願的旋鈕（它沒有 UI），
    # 它在描述「這個帳號同時開幾個 session 不會出事」—— 所以屬於帳號，不屬於條件。
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    claude_code_version: Mapped[str | None] = mapped_column(String(32))

    # `rate_limit_event` 回報的整包 `unifiedWindows`（SPEC §4.6）。
    #
    # 存整包而不是寫死 five_hour／seven_day 兩個 key：Anthropic 加窗的頻率
    # 我們控制不了，而「顯示哪些窗」應該是顯示層的決定，不是一次 migration。
    rate_limit_windows: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 那包資料是什麼時候的。**過期的數字不能當成即時的** —— 代跑者自己在別的
    # 地方也在燒同一個帳號，站台不會知道（SPEC §11 spike #10）。
    quota_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 上次被派到 job 的時間。額度資料還沒有或已過期時，派單用它退回輪流。
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def utilization(self, window: str = "five_hour") -> float | None:
        """某個窗的使用率。沒回報過就是 None，不是 0 —— 那是兩件事。"""
        w = (self.rate_limit_windows or {}).get(window)
        return w.get("utilization") if isinstance(w, dict) else None

    @property
    def usable(self) -> bool:
        return self.oauth_token_enc is not None and not self.needs_reauth


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

    # 指定代跑者；None 代表自動派單。
    # 指到**人**（出借設定），不是帳號 —— 委託者看不到帳號的存在（ADR-0001）。
    requested_lending_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lending_settings.id")
    )
    # 派給誰跑。兩個都記：
    #   lending_id  = 誰的人情債、用誰的條件
    #   account_id  = 實際燒了哪個出借帳號（不對委託者顯示）
    #
    # account_id 存在的理由是「上個月公司帳號跑了幾個 job」這個問題一定會被問到 ——
    # 派單挑 utilization 低的帳號，公司帳號因此是穩定的曝險，而批准者留了痕跡卻
    # 看不到用量等於只留一半（ADR-0001）。
    lending_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lending_settings.id")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lending_accounts.id")
    )

    # 續問：指向被接續的那個 job，以及它留下的 transcript。
    # 為了支援續問，job 容器不再用 --no-session-persistence（SPEC.md §4.2）。
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id")
    )
    transcript_key: Mapped[str | None] = mapped_column(String(512))
    # 借用者上傳的輸入檔。worker 會把它們放進工作目錄，Claude 才有東西可讀 ——
    # 沒有這些，BD 的 job 拿到空目錄卻不報錯，會硬生一份沒有依據的產出。
    attachment_keys: Mapped[list[str]] = mapped_column(JSONB, default=list)
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

    lending: Mapped[LendingSetting | None] = relationship(foreign_keys=[lending_id])
    account: Mapped[LendingAccount | None] = relationship(foreign_keys=[account_id])
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
