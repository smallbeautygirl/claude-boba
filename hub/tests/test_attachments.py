"""附件的存取控制與上限。

存取控制那條是重點：`attachment_keys` 由客戶端指定，不驗 prefix 的話任何人都能
把 key 指到 `jobs/<別人的 job>/output/…`，讓 worker 把別人的產出放進自己的工作
目錄讀走（`.claude/rules/security.md`）。
"""

from __future__ import annotations

import uuid

import pytest
from app import storage
from app.routers.jobs import _check_attachments
from app.schemas import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_TOTAL_BYTES,
    MAX_JOB_INPUT_BYTES,
)
from fastapi import HTTPException


@pytest.fixture
def me():
    from types import SimpleNamespace

    return SimpleNamespace(id=uuid.uuid4())


def _sizes(monkeypatch, mapping: dict[str, int | None]) -> None:
    monkeypatch.setattr(storage, "stat", lambda key: mapping.get(key))


def test_key_of_another_user_is_refused(me, monkeypatch) -> None:
    other = storage.attachment_key(uuid.uuid4(), "secret.xlsx")
    _sizes(monkeypatch, {other: 10})
    with pytest.raises(HTTPException) as exc:
        _check_attachments([other], me, 0)
    assert exc.value.status_code == 404


def test_pointing_at_another_jobs_output_is_refused(me, monkeypatch) -> None:
    """最實際的攻擊：把 key 指到別人 job 的產出 prefix。"""
    stolen = "jobs/00000000-0000-0000-0000-000000000000/output/deck.pptx"
    _sizes(monkeypatch, {stolen: 10})
    with pytest.raises(HTTPException) as exc:
        _check_attachments([stolen], me, 0)
    assert exc.value.status_code == 404


def test_missing_and_not_yours_look_identical(me, monkeypatch) -> None:
    """兩者都回 404，訊息也一樣 —— 分開講等於給人探測別人 job id 的工具。"""
    mine_missing = storage.attachment_key(me.id, "a.txt")
    theirs = storage.attachment_key(uuid.uuid4(), "b.txt")
    _sizes(monkeypatch, {})

    errs = []
    for key in (mine_missing, theirs):
        with pytest.raises(HTTPException) as exc:
            _check_attachments([key], me, 0)
        errs.append((exc.value.status_code, exc.value.detail))
    assert errs[0] == errs[1]


def test_own_key_passes(me, monkeypatch) -> None:
    key = storage.attachment_key(me.id, "報表.xlsx")
    _sizes(monkeypatch, {key: 1024})
    _check_attachments([key], me, 0)


def test_empty_file_is_refused(me, monkeypatch) -> None:
    key = storage.attachment_key(me.id, "a.txt")
    _sizes(monkeypatch, {key: 0})
    with pytest.raises(HTTPException) as exc:
        _check_attachments([key], me, 0)
    assert exc.value.status_code == 400


def test_single_file_over_the_cap(me, monkeypatch) -> None:
    key = storage.attachment_key(me.id, "big.zip")
    _sizes(monkeypatch, {key: MAX_ATTACHMENT_BYTES + 1})
    with pytest.raises(HTTPException) as exc:
        _check_attachments([key], me, 0)
    assert exc.value.status_code == 413


def test_total_over_the_cap(me, monkeypatch) -> None:
    keys = [storage.attachment_key(me.id, f"{i}.bin") for i in range(3)]
    each = MAX_ATTACHMENTS_TOTAL_BYTES // 2
    _sizes(monkeypatch, dict.fromkeys(keys, each))
    with pytest.raises(HTTPException) as exc:
        _check_attachments(keys, me, 0)
    assert exc.value.status_code == 413


def test_exactly_at_the_job_cap_is_allowed(me, monkeypatch) -> None:
    """剛好等於上限要放行。上限是「不得超過」，不是「必須小於」。"""
    key = storage.attachment_key(me.id, "a.bin")
    _sizes(monkeypatch, {key: MAX_ATTACHMENTS_TOTAL_BYTES})
    _check_attachments([key], me, MAX_JOB_INPUT_BYTES - MAX_ATTACHMENTS_TOTAL_BYTES)


def test_transcript_counts_towards_the_job_total(me, monkeypatch) -> None:
    """附件與 transcript 各自都在上限內，合起來仍可能超過 job 的總量。"""
    key = storage.attachment_key(me.id, "a.bin")
    _sizes(monkeypatch, {key: MAX_ATTACHMENTS_TOTAL_BYTES})
    with pytest.raises(HTTPException) as exc:
        _check_attachments(
            [key], me, MAX_JOB_INPUT_BYTES - MAX_ATTACHMENTS_TOTAL_BYTES + 1
        )
    assert exc.value.status_code == 413


def test_duplicate_keys_are_refused(me, monkeypatch) -> None:
    key = storage.attachment_key(me.id, "a.txt")
    _sizes(monkeypatch, {key: 10})
    with pytest.raises(HTTPException) as exc:
        _check_attachments([key, key], me, 0)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\\\Users\\\\me\\\\deck.pptx", "deck.pptx"),
        ("報表.xlsx", "報表.xlsx"),
        ("...", "attachment"),
        ("", "attachment"),
    ],
)
def test_filenames_are_sanitised(raw: str, expected: str) -> None:
    """key 會變成容器裡的檔案路徑 —— 讓使用者控制那段等於讓他寫到工作目錄外。"""
    assert storage.safe_filename(raw) == expected
