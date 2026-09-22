"""把 job transcript 帶回自己的機器。

跟「接著問」是同一件事的兩條路：在站上續、帶回家用自己的額度續。所以開放的條件
與 `can_follow_up` 一致 —— 成功、而且真的留下了 transcript。

這裡的存取控制比 `_get_job` **更緊**：`_get_job` 讓借用者本人與執行該 job 的
出租者都讀得到內容，但下載是把整份對話搬出這個站台，而 SPEC.md §8 的 30 天
lifecycle 是隱私承諾的一部分。搬得走的只有那份對話的主人。
"""

from __future__ import annotations

import uuid

import pytest
from app.enums import JobStatus
from app.models import Job, LendingSetting, User
from app.routers.jobs import _transcript_key_for_download
from fastapi import HTTPException


def _job(
    *,
    borrower_id: uuid.UUID,
    lender_user_id: uuid.UUID | None = None,
    status: JobStatus = JobStatus.SUCCEEDED,
    transcript_key: str | None = "jobs/abc/transcript.jsonl",
) -> Job:
    return Job(
        id=uuid.uuid4(),
        borrower_id=borrower_id,
        status=status,
        transcript_key=transcript_key,
        lending=LendingSetting(owner_user_id=lender_user_id)
        if lender_user_id
        else None,
    )


def test_the_borrower_gets_a_key() -> None:
    uid = uuid.uuid4()
    job = _job(borrower_id=uid)
    assert _transcript_key_for_download(job, User(id=uid)) == job.transcript_key


def test_the_lender_cannot_download_it() -> None:
    """出租者跑了這個 job，讀得到它的內容 —— 但那份對話不是他的。"""
    lender = uuid.uuid4()
    job = _job(borrower_id=uuid.uuid4(), lender_user_id=lender)
    with pytest.raises(HTTPException) as exc:
        _transcript_key_for_download(job, User(id=lender))
    assert exc.value.status_code == 403


def test_a_stranger_cannot_download_it() -> None:
    job = _job(borrower_id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        _transcript_key_for_download(job, User(id=uuid.uuid4()))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "status",
    [JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED, JobStatus.OVER_BUDGET],
)
def test_only_a_successful_job_can_be_taken_home(status: JobStatus) -> None:
    uid = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        _transcript_key_for_download(_job(borrower_id=uid, status=status), User(id=uid))
    assert exc.value.status_code == 404


def test_a_job_that_left_no_transcript_has_nothing_to_give() -> None:
    uid = uuid.uuid4()
    job = _job(borrower_id=uid, transcript_key=None)
    with pytest.raises(HTTPException) as exc:
        _transcript_key_for_download(job, User(id=uid))
    assert exc.value.status_code == 404


def test_it_opens_exactly_when_follow_up_does() -> None:
    """兩顆按鈕在畫面上並排，條件不一致的話其中一顆會是死的。"""
    uid = uuid.uuid4()
    for status in JobStatus:
        for key in ("jobs/abc/transcript.jsonl", None):
            job = _job(borrower_id=uid, status=status, transcript_key=key)
            can_follow_up = job.status.creates_debt and job.transcript_key is not None
            try:
                _transcript_key_for_download(job, User(id=uid))
                can_download = True
            except HTTPException:
                can_download = False
            assert can_download is can_follow_up
