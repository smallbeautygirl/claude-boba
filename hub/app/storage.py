"""MinIO（S3 相容）存取。

Hub 是唯一持有 MinIO 憑證的一方。worker 拿預簽 URL 上傳／下載，
job 容器則完全碰不到 MinIO —— 它的 egress 白名單因此只需要 api.anthropic.com。

預簽 URL 本身就是憑證，效期要短，且不可寫進 log 或長期保存的通知訊息
（.claude/rules/security.md）。
"""

from __future__ import annotations

import functools
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings

# 上傳／下載的預簽效期。job 最長 10 分鐘，給一倍餘裕。
PRESIGN_TTL_SECONDS = 1200


@functools.cache
def _client(endpoint: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def ensure_bucket() -> None:
    client = _client()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        client.create_bucket(Bucket=settings.s3_bucket)


def transcript_key(job_id: uuid.UUID) -> str:
    # 單一 bucket + prefix 分區（SPEC.md §8）。每個 job 一個 prefix，
    # 30 天 lifecycle 自動清 —— 那是隱私承諾的一部分，不是省空間的手段。
    return f"jobs/{job_id}/transcript.jsonl"


def artifact_key(job_id: uuid.UUID, name: str) -> str:
    return f"jobs/{job_id}/output/{name}"


def upload_key(user_id: uuid.UUID) -> str:
    """借用者上傳的 transcript。

    prefix 帶 user id **是存取控制的一部分**，不只是整理。建立 job 時會檢查
    key 的 prefix 與呼叫者相符 —— 否則任何人都能把 transcript_key 指到
    `jobs/<別人的 job>/transcript.jsonl`，讓 worker 把別人的對話 resume 出來。
    """
    return f"uploads/{user_id}/{uuid.uuid4()}.jsonl"


def owns_upload(key: str, user_id: uuid.UUID) -> bool:
    return key.startswith(f"uploads/{user_id}/") and key.endswith(".jsonl")


# 附件與 transcript 分開放，因為歸屬檢查的規則不同（副檔名不限）。
_ATTACH_PREFIX = "attachments"


def attachment_key(user_id: uuid.UUID, filename: str) -> str:
    """借用者上傳的附件。

    保留原始檔名，因為 job 執行時 Claude 會在工作目錄看到它 ——
    `a3f2.bin` 跟 `Q3-營收.xlsx` 對它的幫助差很多。

    檔名先淨化：只取 basename、去掉路徑分隔與 `..`。key 會被直接接成
    容器裡的檔案路徑，讓使用者控制那段等於讓他寫到工作目錄外
    （`.claude/rules/security.md`：絕不把使用者輸入直接接進檔案路徑）。
    """
    return f"{_ATTACH_PREFIX}/{user_id}/{uuid.uuid4()}/{safe_filename(filename)}"


def safe_filename(name: str) -> str:
    cleaned = PurePosixPath(name.replace("\\", "/")).name.strip()
    cleaned = cleaned.lstrip(".") or "attachment"
    return cleaned[:120]


def owns_attachment(key: str, user_id: uuid.UUID) -> bool:
    return key.startswith(f"{_ATTACH_PREFIX}/{user_id}/")


def attachment_name(key: str) -> str:
    return PurePosixPath(key).name


# 許願板的貼圖（docs/web-spec.md §12、ADR-0005）。
#
# ⚠️ **這個 prefix 不適用 30 天 lifecycle**。SPEC.md §8：lifecycle rule 只能掛在
# `jobs/` 上，掛整個 bucket 的話圖會在第 31 天消失而願望還在 —— 牆上一排破圖，
# 沒有人知道為什麼。`wishes/` 的清理方式是「許願板下架時整個 prefix 刪掉」。
#
# prefix 帶的是 **user id 不是 wish id**，跟附件同一條理由：圖在願望被建立**之前**
# 就上傳完了（人是先貼圖再按送出的），那時還沒有 wish id 可用。而 prefix 帶 user id
# 是存取控制的一部分 —— 不驗的話，任何人都能把貼圖的 key 指到別人 job 的產出，
# 讓它出現在一面公開的牆上。
_WISH_PREFIX = "wishes"


def wish_image_key(user_id: uuid.UUID, filename: str) -> str:
    return f"{_WISH_PREFIX}/{user_id}/{uuid.uuid4()}/{safe_filename(filename)}"


def owns_wish_image(key: str, user_id: uuid.UUID) -> bool:
    return key.startswith(f"{_WISH_PREFIX}/{user_id}/")


def get_object(key: str) -> bytes | None:
    """整份讀進記憶體。

    只給許願板的貼圖用，而它上限 5 MB —— job 的產出仍然走預簽 URL，
    50 MB 的檔案沒有理由在 Hub 的記憶體裡轉一手。
    這裡不給預簽是刻意的：一張帶著 prompt 的截圖若有不用登入就打得開的位址，
    外洩得比這面牆本身更遠（ADR-0005）。
    """
    try:
        obj = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError:
        return None
    return obj["Body"].read()


def stat(key: str) -> int | None:
    """回傳物件大小；不存在回 None。"""
    try:
        head = _client().head_object(Bucket=settings.s3_bucket, Key=key)
    except ClientError:
        return None
    return int(head["ContentLength"])


def read_head(key: str, nbytes: int) -> bytes:
    """讀開頭幾個 byte。用來驗格式 —— 不把 50 MB 整份拉進 Hub 的記憶體。"""
    obj = _client().get_object(
        Bucket=settings.s3_bucket, Key=key, Range=f"bytes=0-{nbytes - 1}"
    )
    return obj["Body"].read()


def _signer():
    """簽預簽 URL 用的 client。

    用對外 endpoint 簽，因為拿著這個網址的是瀏覽器或 worker，不是 Hub 自己。
    """
    return _client(settings.s3_public_endpoint or settings.s3_endpoint)


def presign_put(key: str) -> str:
    return _signer().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=PRESIGN_TTL_SECONDS,
    )


def presign_get(key: str) -> str:
    return _signer().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=PRESIGN_TTL_SECONDS,
    )
