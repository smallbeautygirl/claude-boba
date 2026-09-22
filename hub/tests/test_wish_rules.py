"""許願板的規則。四條都是產品決定，不是資料潔癖，所以用測試釘住。

1. **不收 `svg`**，而且不能只信客戶端宣告的 content-type —— 那一行是攻擊者
   自己填的。ADR-0005 把「這是一面會渲染別人上傳內容的公開頁面」寫成拒絕的
   理由，那就得真的去看檔案開頭。
2. **貼圖的 key 帶 user id，而且要驗**。跟附件同一條理由（test_attachments.py）：
   不驗 prefix 的話，任何人都能把 key 指到別人的東西上。
3. **「實現了」的連結必填**。沒有連結它就退化成空頭宣告，跟被砍掉的「認領中」
   變回同一種東西（web-spec §12）。
4. **反應可以是任何 emoji**，但不能變成一個任意字串欄位。
"""

from __future__ import annotations

import uuid

import pytest
from app import storage
from app.routers.wishes import (
    MAX_WISH_IMAGE_BYTES,
    MAX_WISH_IMAGES,
    check_emoji,
    check_images,
    check_link,
    sniff_image,
)
from fastapi import HTTPException

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 28
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
GIF = b"GIF89a" + b"\x00" * 26


@pytest.fixture
def me():
    from types import SimpleNamespace

    return SimpleNamespace(id=uuid.uuid4())


# ── 1. 格式：看檔案開頭，不看客戶端說什麼 ──────────────────────────


@pytest.mark.parametrize(
    ("head", "expected"),
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")],
)
def test_the_three_allowed_formats(head: bytes, expected: str) -> None:
    assert sniff_image(head) == expected


def test_svg_is_refused_however_it_is_labelled() -> None:
    """`svg` 可以帶 script。這是安全限制，不是效能限制（ADR-0005）。"""
    assert sniff_image(SVG) is None


def test_a_png_header_glued_onto_svg_is_still_not_a_png() -> None:
    """魔術位元組要在開頭，不是「出現在某處」。"""
    assert sniff_image(SVG + PNG) is None


def test_other_image_formats_are_refused_too() -> None:
    """白名單是三種，不是「除了 svg 以外都行」。"""
    assert sniff_image(GIF) is None


def test_empty_file_is_not_an_image() -> None:
    assert sniff_image(b"") is None


# ── 2. 歸屬與上限 ────────────────────────────────────────────────


def _stored(monkeypatch, mapping: dict[str, tuple[int, bytes]]) -> None:
    monkeypatch.setattr(
        storage, "stat", lambda key: mapping[key][0] if key in mapping else None
    )
    monkeypatch.setattr(
        storage, "read_head", lambda key, n: mapping.get(key, (0, b""))[1][:n]
    )


def test_own_image_passes(me, monkeypatch) -> None:
    key = storage.wish_image_key(me.id, "畫面.png")
    _stored(monkeypatch, {key: (1024, PNG)})
    assert check_images([key], me) == [(key, "image/png")]


def test_someone_elses_key_is_refused(me, monkeypatch) -> None:
    key = storage.wish_image_key(uuid.uuid4(), "畫面.png")
    _stored(monkeypatch, {key: (1024, PNG)})
    with pytest.raises(HTTPException) as exc:
        check_images([key], me)
    assert exc.value.status_code == 404


def test_a_key_pointing_at_job_output_is_refused(me, monkeypatch) -> None:
    """最實際的攻擊：把貼圖指到別人 job 的產出，讓它出現在一面公開的牆上。"""
    stolen = "jobs/00000000-0000-0000-0000-000000000000/output/deck.pptx"
    _stored(monkeypatch, {stolen: (1024, PNG)})
    with pytest.raises(HTTPException) as exc:
        check_images([stolen], me)
    assert exc.value.status_code == 404


def test_missing_and_not_yours_look_identical(me, monkeypatch) -> None:
    """分開講等於給人一個探測別人上傳了什麼的工具。"""
    mine_missing = storage.wish_image_key(me.id, "a.png")
    theirs = storage.wish_image_key(uuid.uuid4(), "b.png")
    _stored(monkeypatch, {})

    errs = []
    for key in (mine_missing, theirs):
        with pytest.raises(HTTPException) as exc:
            check_images([key], me)
        errs.append((exc.value.status_code, exc.value.detail))
    assert errs[0] == errs[1]


def test_over_the_size_cap(me, monkeypatch) -> None:
    """5 MB，不是 §3 那組 50 MB —— 那是為 .jsonl transcript 訂的。"""
    key = storage.wish_image_key(me.id, "big.png")
    _stored(monkeypatch, {key: (MAX_WISH_IMAGE_BYTES + 1, PNG)})
    with pytest.raises(HTTPException) as exc:
        check_images([key], me)
    assert exc.value.status_code == 413


def test_exactly_at_the_cap_is_allowed(me, monkeypatch) -> None:
    key = storage.wish_image_key(me.id, "a.png")
    _stored(monkeypatch, {key: (MAX_WISH_IMAGE_BYTES, PNG)})
    check_images([key], me)


def test_too_many_images(me, monkeypatch) -> None:
    keys = [
        storage.wish_image_key(me.id, f"{i}.png") for i in range(MAX_WISH_IMAGES + 1)
    ]
    _stored(monkeypatch, dict.fromkeys(keys, (10, PNG)))
    with pytest.raises(HTTPException) as exc:
        check_images(keys, me)
    assert exc.value.status_code == 400


def test_duplicate_keys_are_refused(me, monkeypatch) -> None:
    key = storage.wish_image_key(me.id, "a.png")
    _stored(monkeypatch, {key: (10, PNG)})
    with pytest.raises(HTTPException) as exc:
        check_images([key, key], me)
    assert exc.value.status_code == 400


def test_an_svg_uploaded_as_png_is_refused_at_commit_time(me, monkeypatch) -> None:
    """檔名與 content-type 都是客戶端說了算，內容不是。"""
    key = storage.wish_image_key(me.id, "innocent.png")
    _stored(monkeypatch, {key: (len(SVG), SVG)})
    with pytest.raises(HTTPException) as exc:
        check_images([key], me)
    assert exc.value.status_code == 415


def test_wish_images_live_under_one_prefix(me) -> None:
    """整面牆的圖在同一個 prefix 底下，因為拆牆時要能一次刪掉（SPEC §8）。"""
    assert storage.wish_image_key(me.id, "a.png").startswith("wishes/")


def test_filenames_are_sanitised(me) -> None:
    key = storage.wish_image_key(me.id, "../../etc/passwd")
    assert key.endswith("/passwd")


# ── 3. 「實現了」的連結 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "https://github.com/x/y/pull/3",
        "http://git.internal/c/abc123",
        "  https://a.b/c  ",
    ],
)
def test_a_real_link_passes(raw: str) -> None:
    assert check_link(raw) == raw.strip()


@pytest.mark.parametrize("raw", ["", "   ", "我做完了", "github.com/x/y/pull/3"])
def test_anything_that_is_not_a_link_is_refused(raw: str) -> None:
    """沒有連結，「實現了」就退化成空頭宣告（web-spec §12）。"""
    with pytest.raises(HTTPException) as exc:
        check_link(raw)
    assert exc.value.status_code == 400


def test_javascript_urls_are_refused() -> None:
    """這個連結會被渲染成 <a href>，而牆是公開的。"""
    with pytest.raises(HTTPException) as exc:
        check_link("javascript:alert(1)")
    assert exc.value.status_code == 400


# ── 4. 反應 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw", ["🧋", "👍", "😭", "🎉", "👨\u200d👩\u200d👧\u200d👦", "🇹🇼", "1️⃣", "👍🏽"]
)
def test_any_emoji_is_allowed(raw: str) -> None:
    """選的是自由選，不是固定集合。ZWJ 組合、旗幟、膚色、鍵帽都要過。

    鍵帽（1️⃣）是這裡的地雷：它**裡面有一個 ASCII 的 "1"**，所以用
    `isalnum()` 去擋文字會連它一起擋掉，而它是 picker 裡就有的一顆。
    """
    assert check_emoji(raw) == raw


@pytest.mark.parametrize("raw", ["", " ", "a", "1", "+1", "讚", "🧋 好耶", "x" * 40])
def test_reactions_are_not_a_free_text_field(raw: str) -> None:
    """反應只顯示數字（web-spec §12），它不是一個讓人留話的地方。"""
    with pytest.raises(HTTPException) as exc:
        check_emoji(raw)
    assert exc.value.status_code == 400
