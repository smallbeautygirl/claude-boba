from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from .enums import JobStatus, SourceType, WishCategory, WishTarget

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

# 站台白名單（SPEC §9）。**Fable 在名單上，但不在任何人的預設條件裡**：
# 它的 output 單價是 Haiku 的 10 倍、Sonnet 的 5 倍，同一個 job 用 Haiku 是一杯
# 手搖、用 Fable 就是一頓好料。§9 原本寫「想開放的出租者自己在 .env 加」——
# 那是舊架構的門；job 改到共用主機跑之後，出租者沒有 .env 了，這條白名單是唯一
# 能開那扇門的地方。所以 2026-09-23 把 Fable 放進來，由代跑者在出借條件裡自己勾
# （預設不勾，見 routers/workers.py 的 DEFAULT_MODELS）。
#
# **這份清單必須在伺服器端強制，前端的下拉只是方便。** 2026-09-22 spike #8
# 實測：`--settings availableModels` 根本不擋 model —— 掛 .credentials.json
# 的舊路徑與環境變數 token 的新路徑都一樣，CLI 只用它擋了 fast mode。所以
# 「站台白名單 ∩ 出租者白名單」這條線在 CLI 那層不存在，Hub 是唯一擋得住的
# 位置。不擋的話，借用者直接打 API 帶 model: "opus" 就能用出租者的額度跑
# Opus，讓對方欠十倍的錢。
SITE_MODELS = ("sonnet", "haiku", "fable")

# 新代跑者的預設條件。**沒有 Fable** —— 開放 Fable 是一個要自己按下去的決定，
# 因為燒的是他的額度，而一個 Fable job 撞到 US$5 上限只要幾分鐘。
DEFAULT_MODELS = ["sonnet", "haiku"]


class JobCreate(BaseModel):
    # 借用者身分由 Authorization header 決定，不接受從 body 指定 ——
    # 否則任何人都能用別人的名義掛債。
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str = Field(default="sonnet", max_length=64)
    # 值由 _check_model() 對站台白名單與出租者白名單驗證 ——
    # 這裡不用 Literal，因為錯誤訊息要講得出「這台跑不動」還是「站台不支援」。
    source_type: SourceType = SourceType.PASTE
    # 上傳的 session 檔，來自 POST /api/uploads/transcript。
    # 有帶的話這個 job 就是 `--resume`，不是把對話當文字重貼一次。
    transcript_key: str | None = Field(default=None, max_length=512)
    # 指定代跑者（出借設定 id），不是帳號 —— 委託者看不到帳號（ADR-0001）。
    requested_lending_id: uuid.UUID | None = None
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
    # 派給誰跑。對外只講到**人** —— 是哪個出借帳號跑的不對委託者顯示
    # （ADR-0001）。
    lending_id: uuid.UUID | None
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
    # 送進來的附件。跟 prompt 一起構成「你送的」—— 少了它，job 頁上那段話
    # 常常讀不懂（「把這份做成三頁」—— 哪一份？）。
    # 只有名字與下載連結，**不查大小**：那要對 MinIO 逐檔 HEAD，一個 job 最多
    # 20 個附件，而這一頁會被反覆載入。
    attachments: list[JobAttachment]


class JobAttachment(BaseModel):
    name: str
    download_url: str


class WorkerConfig(BaseModel):
    """領單主機啟動時回報自己。

    ⚠️ **這裡不再回報出借條件**（上限／model／外網）。那些是代跑者在網頁上設的，
    屬於人不屬於主機 —— 讓 worker/.env 回報它們，等於主機每次重啟就把使用者
    在網頁改過的條件蓋掉一次（SPEC §4.12）。
    """

    name: str = Field(default="host", max_length=80)
    max_concurrency: int = 2
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

    # 代跑者的長期 OAuth token（解密後）。託管模型下這是 job 唯一的憑證來源。
    #
    # 🚨 **這個欄位讓整個派單 payload 變成機密。** worker 收到之後要立刻把它
    # 從 dict 裡取出來（見 worker.py 的 run_job），不要讓它跟著 job 到處流動 ——
    # 任何一個 print(job) 都會變成外洩（security.md 紅線 2）。
    oauth_token: str | None = None

    job_id: uuid.UUID
    prompt: str
    model: str
    source_type: SourceType
    job_budget_usd: Decimal
    available_models: list[str]
    # 網路模式現在也隨 job 派下來。舊模型下它在代跑者自己機器的 worker/.env，
    # 但主機只有一台、代跑者有很多位 —— 讓主機的 .env 決定，等於某個人的
    # 「只放行 Claude 本身」被另一個人的設定覆蓋掉（SPEC §4.12）。
    allow_full_network: bool = False
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


# ── 許願板（docs/web-spec.md §12）──────────────────────────────────
#
# **上限不要沿用 §3 那組。** 50 MB / 100 MB 是為 `.jsonl` transcript 訂的，
# 對截圖完全沒有意義。
MAX_WISH_BODY_CHARS = 4000
MAX_WISH_IMAGES = 3
MAX_WISH_IMAGE_BYTES = 5 * 1024 * 1024


class WishInput(BaseModel):
    category: WishCategory
    body: str = Field(min_length=1, max_length=MAX_WISH_BODY_CHARS)
    # 先 PUT 進 MinIO，再把 key 帶過來。歸屬、大小、格式在 check_images() 驗。
    image_keys: list[str] = Field(default_factory=list, max_length=MAX_WISH_IMAGES)


class WishCommentInput(BaseModel):
    body: str = Field(min_length=1, max_length=MAX_WISH_BODY_CHARS)
    image_keys: list[str] = Field(default_factory=list, max_length=MAX_WISH_IMAGES)


class WishEdit(BaseModel):
    """編輯願望只能改分類與字。

    刻意不收 `image_keys`：初版收了卻只寫回 category 與 body，結果是改一個錯字
    就把截圖弄丟 —— 而那正好廢掉「可以編輯」存在的理由（web-spec §12）。
    """

    category: WishCategory
    body: str = Field(min_length=1, max_length=MAX_WISH_BODY_CHARS)


class WishCommentEdit(BaseModel):
    """編輯留言只能改字。

    刻意不收 `image_keys`：收了卻默默丟掉比不支援更糟 —— 使用者會以為換掉了。
    要換圖就刪掉重貼（留言沒有反應以外的東西會被丟掉）。
    """

    body: str = Field(min_length=1, max_length=MAX_WISH_BODY_CHARS)


class WishFulfilInput(BaseModel):
    # 必填。沒有連結，「實現了」就退化成空頭宣告（web-spec §12）。
    # 內容由 check_link() 驗 —— 這裡不用 AnyHttpUrl，因為錯誤訊息要講人話。
    link: str = Field(min_length=1, max_length=1024)


class WishReactionInput(BaseModel):
    target_type: WishTarget
    target_id: uuid.UUID
    # 任何 emoji 都能按（ADR-0004）。是不是一顆 emoji 由 check_emoji() 判。
    emoji: str = Field(min_length=1, max_length=32)


class WishImageUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
