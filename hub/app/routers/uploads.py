"""借用者上傳 Claude Code 的 session 檔（`.jsonl`）。

為什麼要有這個：貼上的對話只是「被讀過」，Claude 不會真的記得當時的環境。
上傳 `.jsonl` 走的是 `claude --resume`，那是真的續跑（SPEC.md §4.2，跨機器
resume 已於 §11 spike #2 驗證）。使用情境就是「我在自己的電腦上跑到一半，
額度用完了」—— 那一刻手上有的正是這個檔案。

**只有 Claude Code 使用者有這種檔案。** claude.ai 與桌面 app 匯不出 `.jsonl`，
所以這條路是 RD 專屬的，BD/PM 的等價路徑是貼上。

檔案直接從瀏覽器 PUT 進 MinIO，不經過 Hub —— 產出檔案的下載已經是這個模式
（預簽 GET 直接給瀏覽器），50 MB 的檔案沒有理由在 Hub 的記憶體裡轉一手。
Hub 在建立 job 時才回頭驗大小與格式（`jobs.attach_transcript`）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import storage
from ..auth import require_user
from ..models import User
from ..schemas import MAX_TRANSCRIPT_BYTES, UploadTicket

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("/transcript", response_model=UploadTicket)
async def create_transcript_upload(user: User = Depends(require_user)) -> UploadTicket:
    key = storage.upload_key(user.id)
    return UploadTicket(
        key=key,
        put_url=storage.presign_put(key),
        max_bytes=MAX_TRANSCRIPT_BYTES,
    )
