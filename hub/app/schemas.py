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
# 產出檔案的上限。超出的部分會被略過並在 UI 說明 —— 悄悄丟掉比擋下更糟。
MAX_ARTIFACTS = 50
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
# 上傳的 session 檔。50 MB 以上的 session，`--resume` 本身就慢到不實用，
# 擋在這裡比讓人等三分鐘再說太大好。
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024
# 驗格式只看開頭這幾行。整份 50 MB 拉進 Hub 解析，只為了確認它是 JSONL，
# 代價與收益不成比例 —— 壞掉的檔案 worker 那邊會明確報錯。
TRANSCRIPT_SNIFF_BYTES = 64 * 1024
TRANSCRIPT_SNIFF_LINES = 5

# 附件（docs/web-spec.md §3）。單一 job 的總量含 transcript 一起算 ——
# 兩個各自 50 MB 但加起來 100 MB 的 job，worker 要搬的還是 100 MB。
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_ATTACHMENTS_TOTAL_BYTES = 50 * 1024 * 1024
MAX_JOB_INPUT_BYTES = 100 * 1024 * 1024
MAX_ATTACHMENTS = 20


class JobCreate(BaseModel):
    # 借用者身分由 Authorization header 決定，不接受從 body 指定 ——
    # 否則任何人都能用別人的名義掛債。
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str = Field(default="sonnet", max_length=64)
    source_type: SourceType = SourceType.PASTE
    # 上傳的 session 檔，來自 POST /api/uploads/transcript。
    # 有帶的話這個 job 就是 `--resume`，不是把對話當文字重貼一次。
    transcript_key: str | None = Field(default=None, max_length=512)
    requested_worker_id: uuid.UUID | None = None
    borrower_cli_version: str | None = Field(default=None, max_length=32)
    # 借用者先把檔案 PUT 進 MinIO，再把 key 帶過來。Hub 在這裡驗歸屬與大小。
    attachment_keys: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class AttachmentUploadRequest(BaseModel):
    # 原始檔名。Hub 會淨化後放進 key —— job 執行時 Claude 會看到這個名字。
    filename: str = Field(min_length=1, max_length=255)


class UploadTicket(BaseModel):
    key: str
    # 預簽 URL 本身就是憑證：效期短，不寫進 log（security.md）。
    put_url: str
    max_bytes: int


class JobSummary(BaseModel):
    id: uuid.UUID
    status: JobStatus
    borrower: str
    # 一段 prompt 摘要。沒有它，列表就是一排 UUID，使用者認不出哪個是哪個。
    preview: str
    is_follow_up: bool
    model: str
    created_at: datetime
    # 第一個事件抵達時才設（CLAIMED → RUNNING）。畫面要靠它把「排隊」與
    # 「執行」分開講 —— 從 created_at 算的話，排隊 10 分鐘、實跑 30 秒的 job
    # 會顯示「已執行 10 分 30 秒」，而債務是照實際花費算的，對不起來。
    started_at: datetime | None
    finished_at: datetime | None
    total_cost_usd: Decimal | None


class JobDetail(JobSummary):
    lender: str | None
    parent_job_id: uuid.UUID | None
    can_follow_up: bool
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
    # 失敗時的分類。成功是 None。欄位見 app/failures.py。
    failure: dict | None
    # 目前這個人能不能中止它（執行中，而且他是跑這個 job 的出租者）。
    can_stop: bool


class WorkerConfig(BaseModel):
    """worker 啟動時回報自己的設定。身分已由 token 決定，所以沒有 name。"""

    allow_full_network: bool = False
    available_models: list[str] = Field(default_factory=lambda: ["sonnet", "haiku"])
    job_budget_usd: Decimal = Decimal(5)
    max_concurrency: int = 1
    claude_code_version: str | None = None


class StopJob(BaseModel):
    """出租者中止一個 job。

    `note` 的價值高於整個停止功能本身（docs/web-spec.md §8）：沒有它，
    停止會被讀成拒絕。
    """

    note: str | None = Field(default=None, max_length=500)


class FollowUp(BaseModel):
    """接著問。上下文由被接續 job 的 transcript 提供。"""

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    # 接著問也可以帶新的附件 —— 跟完一輪之後想再給它一份參考檔案，
    # 是很具體的情境。沒有這個的話輸入列會少一顆 +，使用者會找不到。
    attachment_keys: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


class Attachment(BaseModel):
    name: str
    url: str


class WorkerJob(BaseModel):
    """Hub 派給 worker 的工作。

    檔案都走預簽 URL，worker 因此不需要 MinIO 憑證，job 容器更是完全碰不到。
    """

    job_id: uuid.UUID
    prompt: str
    model: str
    source_type: SourceType
    job_budget_usd: Decimal
    available_models: list[str]
    # 要接續的 transcript（下載用），與這次跑完要把 transcript 放哪（上傳用）。
    resume_from_url: str | None = None
    transcript_put_url: str | None = None
    # 借用者的輸入檔。worker 下載進工作目錄，Claude 才有東西可讀。
    attachments: list[Attachment] = Field(default_factory=list)


class EventBatch(BaseModel):
    """worker 每 ~500ms 批次回報一次。seq 用於去重與續傳。"""

    from_seq: int = Field(ge=0)
    events: list[dict[str, Any]]


class ArtifactDeclaration(BaseModel):
    """worker 宣告它打算上傳哪些檔案，換取預簽 URL。"""

    name: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)


class ArtifactManifest(BaseModel):
    files: list[ArtifactDeclaration] = Field(max_length=MAX_ARTIFACTS)


class JobResult(BaseModel):
    """worker 回報最終結果。欄位對應 stream-json 的 result 事件。"""

    status: JobStatus
    result_text: str | None = None
    total_cost_usd: Decimal | None = None
    model_usage: dict[str, dict[str, Any]] = Field(default_factory=dict)
    error_kind: str | None = None
    error_detail: str | None = None
    lender_cli_version: str | None = None
    transcript_uploaded: bool = False
