"""MinIO（S3 相容）存取。

Hub 是唯一持有 MinIO 憑證的一方。worker 拿預簽 URL 上傳／下載，
job 容器則完全碰不到 MinIO —— 它的 egress 白名單因此只需要 api.anthropic.com。

預簽 URL 本身就是憑證，效期要短，且不可寫進 log 或長期保存的通知訊息
（.claude/rules/security.md）。
"""

from __future__ import annotations

import functools
import uuid

import boto3
from botocore.client import Config

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
