"""資料模型。SPEC.md §6。"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import (
    CredentialKind,
    DebtStatus,
    DebtTier,
    JobStatus,
    SourceType,
    WishCategory,
    WishTarget,
)


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

    def runnable_models(self) -> list[str]:
        """他開放的 model 之中，現在真的派得動的那些。

        「他願意」和「他能」是兩件事，分開存（`available_models` 在人身上、
        `credits_required_models` 在帳號上），**對外一律用兩者的交集** ——
        勾著 Fable 但沒有一個帳號有 credits 的人，不該出現在提交頁的 Fable 選項裡。

        沒有任何可用帳號時原樣回傳他勾的：那種情況的阻礙是授權，不是 credits，
        把它講成 credits 會把人導去買一個不需要買的東西。

        ⚠️ 會碰 `self.accounts`，呼叫前要先 selectinload，否則在 async session
        裡會炸。
        """
        usable = [a for a in self.accounts if a.usable]
        if not usable:
            return list(self.available_models or [])
        return [
            m
            for m in (self.available_models or [])
            if any(m not in (a.credits_required_models or []) for a in usable)
        ]


class LendingAccount(Base):
    """被借出去的那個 Claude 帳號本身。一位代跑者可以有多個。

    帳號持有 token、額度、rate limit 與併發上限 —— 那些是帳號的物理性質。
    條件在 `LendingSetting` 上，因為那是人的態度。

    **對委託者不可見**（ADR-0001）：他挑的是人，Hub 自己決定用哪個帳號跑。
    """

    __tablename__ = "lending_accounts"
    # 同一位代跑者不能把同一個 Claude 帳號出借兩次。Postgres 的唯一索引不擋多個
    # NULL —— 那正是要的：反查不到身分的帳號全是 NULL，不該互相衝突。
    __table_args__ = (
        UniqueConstraint(
            "lending_id", "claude_account_uuid", name="uq_lending_account_claude_uuid"
        ),
    )

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

    # 這個帳號被 Anthropic 回過「這個 model 要買 usage credits」的 model 清單。
    #
    # **這是量到的，不是問到的**（2026-09-23 spike）：站台問不出一個帳號跑不跑得動
    # Fable。`GET /v1/models`、`count_tokens`、`POST /v1/messages` 三個端點，對有
    # Fable 和沒有 Fable 的帳號回的東西**逐字相同**（都是 429 `rate_limit_error`
    # ／`"Error"`）—— 而且有 Fable 的帳號在額度滿的時候也回 429，額度滿正是這個
    # 站台存在的理由。唯一講得出差別的是 CLI 的 `api_error_code: credits_required`，
    # 但那要真的跑一趟：跑不動是 US$0，跑得動要 US$0.22（CLI 自己的 system prompt）。
    #
    # 所以不預先探測，改成**跑失敗一次就記下來**：那一次 0.5 秒、US$0、不計債，
    # 而且答案永遠是最新的。預先探測是花代跑者的錢，去問一個會自己變的餘額。
    #
    # 屬於帳號不屬於條件：「願不願意開 Fable」是人的態度（`available_models`），
    # 「這個帳號現在有沒有 credits」是帳號的物理性質，跟額度同一類（SPEC §4.12）。
    credits_required_models: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # 這是哪一種憑證（security.md 紅線 2）。決定派單前要不要先 refresh。
    credential_kind: Mapped[CredentialKind] = mapped_column(
        _enum(CredentialKind), default=CredentialKind.SETUP_TOKEN
    )
    # 只有 `OAUTH` 那種才有。**絕不離開 Hub** —— 它不進領單回應、不進 worker、
    # 不進容器。派單交出去的永遠是當下那張 access token，不是換票的能力。
    #
    # 它比 access token 值錢得多：access token 八小時就死，這張能一直換出新的，
    # 而且每次 refresh 還會把自己的效期往後推（SPEC §11 #13 實測）。
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # `oauth_token_enc` 那張 access token 什麼時候到期。
    # `SETUP_TOKEN` 的是 None —— 它一年期，而我們不知道確切哪一天。
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 實際拿到的 scope。用來回答「為什麼這個帳號查得到 email、那個查不到」。
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # 這個出借帳號**實際上是哪一個 Claude 帳號**（app/claude_profile.py）。
    #
    # 在這之前站台從頭到尾沒看過帳號的身分：`name` 是代跑者自己打的一串字，
    # 同一個 Claude 帳號授權兩次會變成兩個看起來不相干的帳號，而它們共用同一份
    # 額度與 rate limit —— 派單會以為有兩個池子。
    #
    # `claude_account_uuid` 是去重的鍵，不是 email：email 會改，uuid 不會。
    claude_account_uuid: Mapped[str | None] = mapped_column(String(64), index=True)
    claude_email: Mapped[str | None] = mapped_column(String(200))
    # max / pro / team / enterprise。認不出來就是 None，它只是附註。
    claude_plan: Mapped[str | None] = mapped_column(String(20))
    # **問過了沒有**，跟問到了什麼是兩件事。
    # None = 還沒問過（或反查關著）；有時間但 uuid 是 None = 問過了，Anthropic 不給
    # —— 最可能是 scope：setup-token 產的 token 只有 user:inference。
    # 少了這個欄位，帳號卡上的空白講不出是哪一種。
    claude_identity_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

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
    # worker 最後一次為這個 job 打回來的時間（事件批次或空的心跳，每 ~3 秒一次）。
    # 沒有它，hub 無法分辨「還在跑」與「worker 死了、沒人會來結案」——
    # 2026-09-23 一個 job 就因此停在「執行中」超過半小時（見 app/orphans.py）。
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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


# ── 許願板（docs/web-spec.md §12）──────────────────────────────────
#
# 這四張表是**試玩期的鷹架**，會整組被刪掉。所以它們刻意不跟 jobs 有任何外鍵 ——
# 一則願望不指向任何 job，連 job id 都不存（web-spec §12：不自動帶入任何 job 資訊）。
# 拆牆時 drop 這四張表不會扯到別的東西。


class Wish(Base):
    """牆上的一則。

    **沒有「處理中」這個狀態。** 願望只有兩種：還沒實現的，和實現了的。
    系統不記錄任何人打算做什麼 —— 在一個沒有人被指派任何事的 side project 裡，
    「認領中」記錄的多半是謊言（web-spec §12）。有人在留言裡說「我來做」是
    人講的話，不會變成這裡的一個欄位。
    """

    __tablename__ = "wishes"
    # 三個 fulfilled_* 同生同滅。約束寫在 DB 裡，因為「實現了但沒有連結」正是這個
    # 功能要避免的那個狀態（空頭宣告）—— 應用層擋得住一般路徑，擋不住手動 UPDATE。
    __table_args__ = (
        CheckConstraint(
            "(fulfilled_at IS NULL AND fulfilled_by IS NULL AND fulfilled_link IS NULL)"
            " OR (fulfilled_at IS NOT NULL AND fulfilled_by IS NOT NULL"
            " AND fulfilled_link IS NOT NULL)",
            name="ck_wish_fulfilled_all_or_nothing",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    # 署名是強制的，沒有匿名。五到十個人的團隊裡匿名是假的，而假匿名比署名更糟。
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    category: Mapped[WishCategory] = mapped_column(_enum(WishCategory))
    body: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 三個欄位同生同滅：要嘛全 NULL，要嘛全有值。
    # fulfilled_link 不可為空是產品決定 —— 沒有連結，「實現了」就退化成空頭宣告，
    # 跟被砍掉的「認領中」變回同一種東西。
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    fulfilled_link: Mapped[str | None] = mapped_column(String(1024))

    author: Mapped[User] = relationship(foreign_keys=[author_id])
    fulfiller: Mapped[User | None] = relationship(foreign_keys=[fulfilled_by])
    comments: Mapped[list[WishComment]] = relationship(
        back_populates="wish", cascade="all, delete-orphan"
    )


class WishComment(Base):
    """願望底下的一則留言。單層，沒有回覆的回覆。

    巢狀在五到十人的牆上永遠用不到，但會讓版面、資料模型、通知三處都變複雜。
    """

    __tablename__ = "wish_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    wish_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wishes.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User] = relationship()
    wish: Mapped[Wish] = relationship(back_populates="comments")


class WishReaction(Base):
    """按在願望或留言上的 emoji。

    **只顯示數字，不顯示是誰按的**（web-spec §12），但這裡仍然要存 user_id ——
    沒有它就做不到「同一顆再按一次是取消」，也擋不住同一個人灌一百次。
    回應給前端時只回聚合數字與「我按過了沒有」。
    """

    __tablename__ = "wish_reactions"
    __table_args__ = (
        UniqueConstraint(
            "target_type", "target_id", "user_id", "emoji", name="uq_wish_reaction"
        ),
        Index("ix_wish_reactions_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    target_type: Mapped[WishTarget] = mapped_column(_enum(WishTarget))
    # 刻意不設外鍵：它指向兩張表之一。願望刪掉時由 router 一併清掉。
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    # 任何 emoji 都能按（ADR-0004），所以這裡不是 enum。長度由 check_emoji() 擋。
    emoji: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WishImage(Base):
    """願望或留言的貼圖。檔案在 MinIO 的 `wishes/` prefix。

    這一條弱化了 SPEC §4.3，是知情的取捨 —— 完整論述在 ADR-0005。
    **不給預簽 URL**：讀取走 hub 一個要登入的端點，圖片的可見範圍必須等於牆的
    可見範圍。
    """

    __tablename__ = "wish_images"
    __table_args__ = (Index("ix_wish_images_target", "target_type", "target_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    target_type: Mapped[WishTarget] = mapped_column(_enum(WishTarget))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    key: Mapped[str] = mapped_column(String(768))
    # 伺服器**看檔案開頭**決定的，不是客戶端宣告的 —— 那一行是攻擊者自己填的。
    content_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
