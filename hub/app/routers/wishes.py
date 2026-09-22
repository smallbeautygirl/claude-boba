"""許願板。docs/web-spec.md §12。

**這整支是試玩期的鷹架**，有寫死的下架條件（Phase 2 驗完，或連續 30 天沒有新的
一則，先到者為準）。拆的時候是刪掉這個檔案、四張表、MinIO 的 `wishes/` prefix，
以及前端那個相依 —— 所以它刻意不跟 job 有任何關聯，連 job id 都不收。

三個決定在讀這份程式之前要先知道，否則會覺得少寫了東西：

- **沒有「處理中」。** 只有「實現了」，而且連結必填。沒有連結它就退化成空頭宣告
- **反應只回聚合數字**，不回是誰按的 —— 雖然 DB 裡存得到
- **通知一律不含內容**，見 notify.py
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import notify, storage
from ..auth import require_user
from ..db import get_session
from ..enums import WishCategory, WishTarget
from ..models import User, Wish, WishComment, WishImage, WishReaction
from ..schemas import (
    MAX_WISH_IMAGE_BYTES,
    MAX_WISH_IMAGES,
    UploadTicket,
    WishCommentEdit,
    WishCommentInput,
    WishEdit,
    WishFulfilInput,
    WishImageUploadRequest,
    WishInput,
    WishReactionInput,
)

router = APIRouter(prefix="/api/wishes", tags=["wishes"])

# 分類的文案。四類的界線是模糊的（「壞掉了」vs「體驗不好」），那是選四類的已知
# 代價 —— 補償是願望可以編輯，含分類。
CATEGORY_LABEL: dict[WishCategory, str] = {
    WishCategory.BROKEN: "壞掉了",
    WishCategory.WANT_COMMAND: "想要一個指令",
    WishCategory.ROUGH_EDGE: "體驗不好",
    WishCategory.OTHER: "其他",
}

# 只收這三種。**不收 `svg`** —— 它可以帶 script，而這是一面會渲染別人上傳內容的
# 公開頁面。這是安全限制，不是效能限制（ADR-0005）。
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_SNIFF_BYTES = 16

# 一顆 emoji 允許出現的 Unicode 分類。Lo（「讚」）與 Ll（"a"）不在裡面，
# 那正是要擋的東西。
_EMOJI_CATEGORIES = frozenset({"So", "Sk", "Mn", "Me", "Cf", "Nd"})


def sniff_image(head: bytes) -> str | None:
    """看檔案開頭決定格式。

    **不能只信客戶端宣告的 content-type**，那一行是上傳的人自己填的；
    副檔名同理。要擋 `svg` 就得真的去看內容。
    """
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    # WEBP 是 RIFF 容器：前四個 byte 是 RIFF，第 8–12 個是 WEBP。
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_images(keys: list[str], user: User) -> list[tuple[str, str]]:
    """驗歸屬、張數、大小、格式。回傳 (key, content_type)。

    歸屬那條跟附件同一條理由（test_attachments.py）：key 由客戶端指定，
    不驗 prefix 的話任何人都能把它指到別人 job 的產出，**讓它出現在一面公開的
    牆上**。「不存在」與「不是你的」回一樣的 404 —— 分開講等於給人一個探測工具。
    """
    if len(keys) > MAX_WISH_IMAGES:
        raise HTTPException(status_code=400, detail=f"最多 {MAX_WISH_IMAGES} 張圖")
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=400, detail="同一張圖重複了")

    out: list[tuple[str, str]] = []
    for key in keys:
        if not storage.owns_wish_image(key, user.id):
            raise HTTPException(status_code=404, detail="找不到這個檔案")
        size = storage.stat(key)
        if size is None or size == 0:
            raise HTTPException(status_code=404, detail="找不到這個檔案")
        if size > MAX_WISH_IMAGE_BYTES:
            mb = MAX_WISH_IMAGE_BYTES // (1024 * 1024)
            raise HTTPException(status_code=413, detail=f"單張圖不能超過 {mb} MB")
        kind = sniff_image(storage.read_head(key, _SNIFF_BYTES))
        if kind is None:
            raise HTTPException(
                status_code=415, detail="只收 PNG、JPEG、WebP —— 換個格式再試一次"
            )
        out.append((key, kind))
    return out


def check_link(raw: str) -> str:
    """「實現了」的 PR 或 commit 連結。**必填。**

    沒有連結，「實現了」就退化成空頭宣告，跟被砍掉的「認領中」變回同一種東西
    （web-spec §12）。只收 http(s)：這個值會被渲染成 `<a href>`，而牆是公開的。
    """
    link = (raw or "").strip()
    if not link.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400, detail="要附上 PR 或 commit 的連結（http/https）"
        )
    return link


def check_emoji(raw: str) -> str:
    """任何 emoji 都能按（ADR-0004），但這不是一個讓人留話的欄位。

    反應只顯示數字（web-spec §12），所以它必須真的只是一顆符號 ——
    放行任意字串的話，牆上會出現一排「假裝成反應的留言」，而那些字沒有人在審。
    """
    emoji = (raw or "").strip()
    if not emoji or len(emoji) > 16:
        raise HTTPException(status_code=400, detail="這不是一顆 emoji")
    # 沒有一個乾淨的「是不是 emoji」判準，所以用 Unicode 分類逼近：
    # 符號（So/Sk）、修飾字（Mn/Me，膚色與 ⃣）、零寬連接（Cf）、數字（Nd，鍵帽的
    # 那個「1」）以外一律不是。**初版用 `isalnum()` 擋，那會連 1️⃣ 也擋掉** ——
    # 而規格說的是任何 emoji 都能按。
    #
    # 至少要有一個非 ASCII 的字，否則 "1"、"+1" 這種會從 Nd 那個洞鑽過去。
    if any(unicodedata.category(c) not in _EMOJI_CATEGORIES for c in emoji):
        raise HTTPException(status_code=400, detail="這不是一顆 emoji")
    if all(ord(c) < 128 for c in emoji):
        raise HTTPException(status_code=400, detail="這不是一顆 emoji")
    return emoji


# ── 讀 ───────────────────────────────────────────────────────────


def _image_view(img: WishImage) -> dict:
    # 給的是 hub 的端點，不是預簽 URL。圖片的可見範圍必須等於牆的可見範圍
    # （ADR-0005）—— 一張帶著 prompt 的截圖若有不用登入就打得開的位址，
    # 外洩得比這面牆本身更遠。
    return {"id": str(img.id), "url": f"/api/wishes/images/{img.id}"}


def _reaction_view(rows: list[WishReaction], me: uuid.UUID) -> list[dict]:
    """聚合。**不回是誰按的** —— 只回數字與「我按過了沒有」。

    五到十人的團隊裡「他對我的願望按了 👍，對別人的按了 🎉」是真的會被比較的。
    """
    counts: dict[str, dict] = {}
    for r in rows:
        slot = counts.setdefault(r.emoji, {"emoji": r.emoji, "count": 0, "mine": False})
        slot["count"] += 1
        if r.user_id == me:
            slot["mine"] = True
    return sorted(counts.values(), key=lambda c: (-c["count"], c["emoji"]))


def _comment_view(c: WishComment, *, me: uuid.UUID, extras: _Extras) -> dict:
    return {
        "id": str(c.id),
        "author": c.author.display_name,
        "mine": c.author_id == me,
        "body": c.body,
        "created_at": c.created_at.isoformat(),
        "images": [_image_view(i) for i in extras.images(WishTarget.COMMENT, c.id)],
        "reactions": _reaction_view(extras.reactions(WishTarget.COMMENT, c.id), me),
    }


def _wish_view(w: Wish, *, me: uuid.UUID, extras: _Extras) -> dict:
    return {
        "id": str(w.id),
        "category": str(w.category),
        "category_label": CATEGORY_LABEL[w.category],
        "author": w.author.display_name,
        "mine": w.author_id == me,
        "body": w.body,
        "created_at": w.created_at.isoformat(),
        # 實現了的不消失、不收進第二個分頁 —— 它是這面牆唯一的活著證據。
        "fulfilled": (
            {
                "at": w.fulfilled_at.isoformat(),
                "by": w.fulfiller.display_name if w.fulfiller else "某人",
                "link": w.fulfilled_link,
            }
            if w.fulfilled_at
            else None
        ),
        "images": [_image_view(i) for i in extras.images(WishTarget.WISH, w.id)],
        "reactions": _reaction_view(extras.reactions(WishTarget.WISH, w.id), me),
        "comments": [
            _comment_view(c, me=me, extras=extras)
            for c in sorted(w.comments, key=lambda c: c.created_at)
        ],
    }


class _Extras:
    """反應與貼圖一次撈完，不要在每一則願望裡各打一次 query。"""

    def __init__(self, reactions: list[WishReaction], images: list[WishImage]) -> None:
        self._r: dict[tuple[str, uuid.UUID], list[WishReaction]] = {}
        self._i: dict[tuple[str, uuid.UUID], list[WishImage]] = {}
        for r in reactions:
            self._r.setdefault((str(r.target_type), r.target_id), []).append(r)
        for i in images:
            self._i.setdefault((str(i.target_type), i.target_id), []).append(i)

    def reactions(self, kind: WishTarget, tid: uuid.UUID) -> list[WishReaction]:
        return self._r.get((str(kind), tid), [])

    def images(self, kind: WishTarget, tid: uuid.UUID) -> list[WishImage]:
        return self._i.get((str(kind), tid), [])


async def _extras(session: AsyncSession) -> _Extras:
    # 鷹架，而且五到十個人 —— 整面牆一次撈完比寫兩個帶 IN 的 query 好懂。
    return _Extras(
        list(await session.scalars(select(WishReaction))),
        list(await session.scalars(select(WishImage))),
    )


@router.get("")
async def board(
    category: WishCategory | None = None,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """整面牆。

    **預設依最新排**，不是依熱門。依熱門排會讓剛貼的那則一出生就在第五名，
    而這面牆要解的痛點正是回饋量不足 —— 那一則是最需要被看到的。
    實現了的沉到底部。
    """
    stmt = (
        select(Wish)
        .options(
            selectinload(Wish.author),
            selectinload(Wish.fulfiller),
            selectinload(Wish.comments).selectinload(WishComment.author),
        )
        .order_by(Wish.fulfilled_at.is_(None).desc(), Wish.created_at.desc())
    )
    if category is not None:
        stmt = stmt.where(Wish.category == category)

    rows = list(await session.scalars(stmt))
    extras = await _extras(session)
    return {
        "items": [_wish_view(w, me=user.id, extras=extras) for w in rows],
        "categories": [
            {"value": str(c), "label": CATEGORY_LABEL[c]} for c in WishCategory
        ],
    }


@router.get("/images/{image_id}")
async def image(
    image_id: uuid.UUID,
    _: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """貼圖。**要登入。**

    用 image id 取，不是用 key —— key 由客戶端帶就又多一條要驗 prefix 的路徑，
    而這裡完全不需要讓客戶端知道 key 長什麼樣。
    """
    img = await session.get(WishImage, image_id)
    if img is None:
        raise HTTPException(status_code=404, detail="找不到這張圖")
    blob = storage.get_object(img.key)
    if blob is None:
        raise HTTPException(status_code=404, detail="找不到這張圖")
    return Response(
        content=blob,
        media_type=img.content_type,
        # 牆是登入才看得到的，圖也一樣 —— 不要讓它被共用快取存起來。
        headers={
            "Cache-Control": "private, max-age=3600",
            # 吐出去的是使用者上傳的 bytes。少了這個，瀏覽器會自己嗅探型別 ——
            # 而我們花了 sniff_image() 的力氣就是為了不讓它被當成別的東西。
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── 寫 ───────────────────────────────────────────────────────────


@router.post("/uploads/image", response_model=UploadTicket)
async def create_image_upload(
    body: WishImageUploadRequest, user: User = Depends(require_user)
) -> UploadTicket:
    """要一張貼圖的上傳票券。一次一張。

    格式與大小在**送出願望時**才驗（`check_images`），不是在這裡 ——
    這時候檔案還沒上去。
    """
    key = storage.wish_image_key(user.id, body.filename)
    return UploadTicket(
        key=key, put_url=storage.presign_put(key), max_bytes=MAX_WISH_IMAGE_BYTES
    )


@router.post("", status_code=201)
async def create_wish(
    body: WishInput,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    images = check_images(body.image_keys, user)
    wish = Wish(author_id=user.id, category=body.category, body=body.body.strip())
    session.add(wish)
    await session.flush()
    _attach(session, WishTarget.WISH, wish.id, images)
    await session.commit()

    notify.wish_created(user, wish.id, CATEGORY_LABEL[wish.category])
    return {"id": str(wish.id)}


@router.patch("/{wish_id}")
async def edit_wish(
    wish_id: uuid.UUID,
    body: WishEdit,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """編輯自己的願望，**含分類**。

    分類可改不是可有可無的：四類的界線模糊，選錯一定會發生，而不能編輯的話唯一
    的修正方式是刪掉重貼 —— 那會丟掉已經累積的反應和留言。

    **編輯不碰貼圖。** 初版收了 `image_keys` 卻只寫回 category 與 body，結果是
    改一個錯字就把截圖弄丟 —— 那正好廢掉「可以編輯」存在的理由。要換圖就刪掉重貼，
    而那個代價是明講的，不是悄悄發生的。

    **不留編輯歷史，也不對外顯示「已編輯」**（web-spec §12）。`updated_at` 有存，
    但它不進任何回應 —— 鷹架不需要審計軌跡，而在一個全部署名的牆上，偷改內容的
    社交成本本來就夠高了。掛一個「改過」的標記只會讓修錯字的人看起來心虛。
    """
    wish = await _wish(wish_id, session)
    _require_author(wish.author_id, user)
    wish.category = body.category
    wish.body = body.body.strip()
    wish.updated_at = datetime.now(UTC)
    await session.commit()
    return {"id": str(wish.id)}


@router.delete("/{wish_id}", status_code=204)
async def delete_wish(
    wish_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    wish = await _wish(wish_id, session)
    _require_author(wish.author_id, user)
    comment_ids = list(
        await session.scalars(
            select(WishComment.id).where(WishComment.wish_id == wish.id)
        )
    )
    # 反應與貼圖刻意沒有外鍵（它們指向兩張表之一），所以要自己收。
    await _purge(session, [(WishTarget.WISH, wish.id)])
    await _purge(session, [(WishTarget.COMMENT, cid) for cid in comment_ids])
    await session.delete(wish)
    await session.commit()
    return Response(status_code=204)


@router.post("/{wish_id}/fulfil")
async def fulfil(
    wish_id: uuid.UUID,
    body: WishFulfilInput,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """標成「實現了」。**任何人都能標**，連結必填。

    不做權限模型是刻意的：鷹架、五到十人，加權限的成本高於被亂標的成本。
    真正擋住亂標的是那個必填的連結，不是權限。
    """
    wish = await _wish(wish_id, session)
    link = check_link(body.link)
    already = wish.fulfilled_at is not None
    wish.fulfilled_at = datetime.now(UTC)
    wish.fulfilled_by = user.id
    wish.fulfilled_link = link
    await session.commit()

    # 重複標同一則不再通知一次 —— 換個連結是修正，不是新消息。
    if not already and wish.author_id != user.id:
        notify.wish_fulfilled(wish.author, wish.id, user.display_name)
    return {"id": str(wish.id)}


@router.delete("/{wish_id}/fulfil")
async def unfulfil(
    wish_id: uuid.UUID,
    _: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """標錯了可以取消。三個欄位同生同滅。"""
    wish = await _wish(wish_id, session)
    wish.fulfilled_at = None
    wish.fulfilled_by = None
    wish.fulfilled_link = None
    await session.commit()
    return {"id": str(wish.id)}


@router.post("/{wish_id}/comments", status_code=201)
async def add_comment(
    wish_id: uuid.UUID,
    body: WishCommentInput,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    wish = await _wish(wish_id, session)
    images = check_images(body.image_keys, user)
    comment = WishComment(wish_id=wish.id, author_id=user.id, body=body.body.strip())
    session.add(comment)
    await session.flush()
    _attach(session, WishTarget.COMMENT, comment.id, images)
    await session.commit()

    # 不通知自己回自己。
    if wish.author_id != user.id:
        notify.wish_commented(wish.author, wish.id, user.display_name)
    return {"id": str(comment.id)}


@router.patch("/comments/{comment_id}")
async def edit_comment(
    comment_id: uuid.UUID,
    body: WishCommentEdit,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    comment = await _comment(comment_id, session)
    _require_author(comment.author_id, user)
    comment.body = body.body.strip()
    comment.updated_at = datetime.now(UTC)
    await session.commit()
    return {"id": str(comment.id)}


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    comment = await _comment(comment_id, session)
    _require_author(comment.author_id, user)
    await _purge(session, [(WishTarget.COMMENT, comment.id)])
    await session.delete(comment)
    await session.commit()
    return Response(status_code=204)


@router.post("/reactions")
async def react(
    body: WishReactionInput,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """按一顆 emoji。**同一顆再按一次是取消**（Teams / Slack 的既有語彙）。

    一個人可以按多顆不同的 —— 所以取消的判斷是 (目標, 人, emoji) 四元組，
    不是「這個人在這則上的那一顆」。
    """
    emoji = check_emoji(body.emoji)
    await _target_exists(body.target_type, body.target_id, session)

    existing = await session.scalar(
        select(WishReaction).where(
            WishReaction.target_type == body.target_type,
            WishReaction.target_id == body.target_id,
            WishReaction.user_id == user.id,
            WishReaction.emoji == emoji,
        )
    )
    if existing is not None:
        await session.delete(existing)
        await session.commit()
        return {"emoji": emoji, "mine": False}

    session.add(
        WishReaction(
            target_type=body.target_type,
            target_id=body.target_id,
            user_id=user.id,
            emoji=emoji,
        )
    )
    await session.commit()
    return {"emoji": emoji, "mine": True}


# ── 小工具 ───────────────────────────────────────────────────────


def _attach(
    session: AsyncSession,
    kind: WishTarget,
    target_id: uuid.UUID,
    images: list[tuple[str, str]],
) -> None:
    for key, content_type in images:
        session.add(
            WishImage(
                target_type=kind,
                target_id=target_id,
                key=key,
                content_type=content_type,
            )
        )


async def _purge(
    session: AsyncSession, targets: list[tuple[WishTarget, uuid.UUID]]
) -> None:
    for kind, tid in targets:
        for table in (WishReaction, WishImage):
            await session.execute(
                delete(table).where(table.target_type == kind, table.target_id == tid)
            )


def _require_author(author_id: uuid.UUID, user: User) -> None:
    """編輯與刪除只有本人 —— 這是唯一有權限判斷的地方。

    「實現了」刻意不在這裡：那一個任何人都能標。
    """
    if author_id != user.id:
        raise HTTPException(status_code=403, detail="只能改自己貼的")


async def _wish(wish_id: uuid.UUID, session: AsyncSession) -> Wish:
    wish = await session.get(
        Wish, wish_id, options=[selectinload(Wish.author), selectinload(Wish.fulfiller)]
    )
    if wish is None:
        raise HTTPException(status_code=404, detail="找不到這則願望")
    return wish


async def _comment(comment_id: uuid.UUID, session: AsyncSession) -> WishComment:
    comment = await session.get(WishComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="找不到這則留言")
    return comment


async def _target_exists(
    kind: WishTarget, target_id: uuid.UUID, session: AsyncSession
) -> None:
    """反應沒有外鍵（它指向兩張表之一），所以這裡要自己確認目標還在。

    不擋的話，wish_reactions 會長出一堆指向已刪除東西的孤兒列。
    """
    model = Wish if kind is WishTarget.WISH else WishComment
    if await session.get(model, target_id) is None:
        raise HTTPException(status_code=404, detail="找不到要按的東西")
