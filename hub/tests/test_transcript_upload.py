"""上傳的 session 檔（`.jsonl`）。

第一組測試是**存取控制**，不是格式檢查：`transcript_key` 由客戶端指定，
不驗 prefix 的話任何人都能把它指到別人 job 的 transcript，讓 worker 把
那段對話 resume 出來。security.md 說只有借用者本人與執行該 job 的出租者
能讀該 job 的內容 —— 這裡就是那條規則的落點。
"""

from __future__ import annotations

import uuid

import pytest
from app import storage
from app.models import User
from app.routers.jobs import _check_transcript
from app.schemas import MAX_TRANSCRIPT_BYTES
from fastapi import HTTPException


def _user() -> User:
    return User(id=uuid.uuid4())


class _FakeStore:
    """只回答 _check_transcript 會問的那三件事。"""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def owns_upload(self, key: str, user_id: uuid.UUID) -> bool:
        return storage.owns_upload(key, user_id)

    def stat(self, key: str) -> int | None:
        blob = self.blobs.get(key)
        return None if blob is None else len(blob)

    def read_head(self, key: str, nbytes: int) -> bytes:
        return self.blobs[key][:nbytes]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    def install(blobs: dict[str, bytes]) -> None:
        monkeypatch.setattr("app.routers.jobs.storage", _FakeStore(blobs))

    return install


# --- 存取控制 -------------------------------------------------------------


def test_upload_key_is_scoped_to_the_user() -> None:
    uid = uuid.uuid4()
    key = storage.upload_key(uid)
    assert storage.owns_upload(key, uid)
    assert not storage.owns_upload(key, uuid.uuid4())


def test_another_users_upload_is_refused() -> None:
    mine, theirs = _user(), _user()
    key = storage.upload_key(theirs.id)
    with pytest.raises(HTTPException) as e:
        _check_transcript(key, mine)
    assert e.value.status_code == 404


def test_pointing_at_a_jobs_transcript_is_refused() -> None:
    """最要緊的一條：不能把 key 指到別人 job 的產出 transcript。"""
    victim = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        _check_transcript(storage.transcript_key(victim), _user())
    assert e.value.status_code == 404


def test_missing_and_foreign_are_the_same_error(store) -> None:
    """兩者都回 404 且訊息相同 —— 分開講等於給人探測別人 job id 的工具。"""
    me = _user()
    store({})
    missing = storage.upload_key(me.id)
    foreign = storage.upload_key(uuid.uuid4())

    errors = []
    for key in (missing, foreign):
        with pytest.raises(HTTPException) as e:
            _check_transcript(key, me)
        errors.append((e.value.status_code, e.value.detail))
    assert errors[0] == errors[1]


# --- 格式與大小 -----------------------------------------------------------


def test_a_real_session_file_passes(store) -> None:
    me = _user()
    key = storage.upload_key(me.id)
    store({key: b'{"type":"user","message":{"role":"user"}}\n{"type":"assistant"}\n'})
    _check_transcript(key, me)  # 不拋就是過


def test_empty_file_is_refused(store) -> None:
    me = _user()
    key = storage.upload_key(me.id)
    store({key: b""})
    with pytest.raises(HTTPException) as e:
        _check_transcript(key, me)
    assert e.value.status_code == 400
    assert "空" in e.value.detail


def test_oversized_file_is_refused_before_it_is_read(store) -> None:
    me = _user()
    key = storage.upload_key(me.id)
    store({key: b"x" * (MAX_TRANSCRIPT_BYTES + 1)})
    with pytest.raises(HTTPException) as e:
        _check_transcript(key, me)
    assert e.value.status_code == 413
    assert "50 MB" in e.value.detail


def test_not_jsonl_says_where_to_find_the_real_thing(store) -> None:
    """錯誤訊息要給下一步。只說「格式錯誤」，人不知道該去哪找檔案。"""
    me = _user()
    key = storage.upload_key(me.id)
    store({key: "這是一份純文字的對話紀錄\n不是 JSONL\n".encode()})
    with pytest.raises(HTTPException) as e:
        _check_transcript(key, me)
    assert e.value.status_code == 400
    assert ".claude/projects" in e.value.detail


def test_truncated_last_line_does_not_fail_validation(store) -> None:
    """只讀開頭 64 KB，最後一行幾乎一定是斷的 —— 不能因此判檔案壞掉。"""
    me = _user()
    key = storage.upload_key(me.id)
    good = b'{"type":"user"}\n' * 8
    store({key: good + b'{"type":"assis'})
    _check_transcript(key, me)


def test_single_line_file_is_still_validated(store) -> None:
    """整份只有一行時沒有「前面幾行」可驗，但它仍然必須是 JSON。"""
    me = _user()
    key = storage.upload_key(me.id)
    store({key: b"not json at all"})
    with pytest.raises(HTTPException) as e:
        _check_transcript(key, me)
    assert e.value.status_code == 400
