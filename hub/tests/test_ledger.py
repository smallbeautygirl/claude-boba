"""帳本走完一整圈：兩個人、一筆真的債、真的資料庫。

這一組存在的理由很實際 —— 站台目前只有一個使用者，而帳本的每一條規則都
**需要兩個人才看得見**：誰在「你欠別人」、誰在「別人欠你」、誰按得動哪顆按鈕。
一個人登入只會看到「乾乾淨淨，不欠任何人」，那畫面對的時候和錯的時候長得一模一樣。

釘住的是 SPEC §4.8 的四條：

1. **同一筆債，兩邊看到的方向相反**，而且各自看到的是對方的名字。
2. **結清只有債主能按**，借用者按會被擋在後端（前端不渲染不等於沒送出去）。
3. **「我請過了」只有欠債的人能按**，而且它不等於結清 —— 債還在。
4. **結清是債主單方面宣告的，借用者不能把它撤銷掉。** 這條是 2026-09-23
   實測撞出來的：戳一筆已結清的債會讓它跳回「別人欠你」，而債主不會知道。

另外釘住兩則通知真的會送。它們不是禮貌 —— 結清與戳都是**單方面宣告**，
對方不去開網頁就不會知道，那顆按鈕按下去等於沒有作用（web-spec §11）。
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.auth import require_user
from app.db import engine
from app.enums import DebtStatus, DebtTier, JobStatus, SourceType
from app.main import app
from app.models import Debt, Job, LendingSetting, User
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings  # isort: skip


def _run(coro):
    """在自己的事件迴圈與自己的連線池上做資料準備。

    不共用 `app.db.engine` —— 那個池子會留著上一個迴圈的連線，
    跟 TestClient 混用就會拿到「attached to a different loop」。
    """

    async def go():
        eng = create_async_engine(settings.database_url)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                return await coro(s)
        finally:
            await eng.dispose()

    return asyncio.run(go())


def _client(user: User) -> TestClient:
    """以這個人的身分打 API。

    每個 TestClient 跑在自己的事件迴圈上，而 `app.db.engine` 的池子會留著上一個
    迴圈的連線 —— 兩個人輪流打同一支端點時不倒掉，第二個會拿到
    「Event loop is closed」。這一組測試本質上就是**兩個人來回**，所以每次都倒。
    """
    engine.sync_engine.pool.dispose()
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_pool():
    engine.sync_engine.pool.dispose()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def pair():
    """借用者、債主，以及他們之間一筆 US$4 的債（☕️ 級距）。"""
    tag = uuid.uuid4().hex[:8]

    async def build(s):
        borrower = User(
            observ_user_id=_fake_observ_id(),
            email=f"borrower-{tag}@example.com",
            display_name=f"借用者{tag}",
        )
        lender = User(
            observ_user_id=_fake_observ_id(),
            email=f"lender-{tag}@example.com",
            display_name=f"債主{tag}",
        )
        s.add_all([borrower, lender])
        await s.flush()

        lending = LendingSetting(owner_user_id=lender.id, available_models=["sonnet"])
        s.add(lending)
        await s.flush()

        job = Job(
            borrower_id=borrower.id,
            source_type=SourceType.PASTE,
            prompt="跑個東西",
            status=JobStatus.SUCCEEDED,
            lending_id=lending.id,
            total_cost_usd=Decimal("4.0"),
        )
        s.add(job)
        await s.flush()

        debt = Debt(
            job_id=job.id,
            borrower_id=borrower.id,
            lender_id=lender.id,
            amount_usd=Decimal("4.0"),
            tier=DebtTier.COFFEE,
            status=DebtStatus.OPEN,
            # 三天前欠的：「已經幾天了」是畫面上唯一的社交壓力來源，得是真的。
            created_at=datetime.now(UTC) - timedelta(days=3),
        )
        s.add(debt)
        await s.commit()
        return borrower, lender, debt.id, job.id, lending.id

    borrower, lender, debt_id, job_id, lending_id = _run(build)
    yield borrower, lender, debt_id

    async def drop(s):
        # 收得乾淨一點：這些測試跑在開發資料庫上，留下來的假人會出現在
        # 排行榜與派單下拉選單裡 —— 那比測試本身更難debug。
        for model, key in (
            (Debt, debt_id),
            (Job, job_id),
            (LendingSetting, lending_id),
            (User, borrower.id),
            (User, lender.id),
        ):
            row = await s.get(model, key)
            if row is not None:
                await s.delete(row)
        await s.commit()

    _run(drop)


def _fake_observ_id() -> int:
    """`observ_user_id` 有唯一索引，而這組測試每跑一次都要兩個新的人。

    範圍取在真實 Observ id 不可能到達的高位，避免撞到開發資料庫裡真的使用者。
    """
    return random.randrange(1_000_000_000, 2_000_000_000)


def _find(ledger: dict, section: str, debt_id) -> dict | None:
    return next((d for d in ledger[section] if d["id"] == str(debt_id)), None)


# ── 1. 一筆債，兩邊看到的是相反的方向 ───────────────────────────────


def test_the_same_debt_reads_opposite_on_each_side(pair) -> None:
    borrower, lender, debt_id = pair

    mine = _client(borrower).get("/api/ledger").json()
    theirs = _client(lender).get("/api/ledger").json()

    owe = _find(mine, "i_owe", debt_id)
    owed = _find(theirs, "owed_to_me", debt_id)
    assert owe is not None, "借用者應該在「你欠別人」看到這筆"
    assert owed is not None, "債主應該在「別人欠你」看到這筆"

    # 各自看到的是**對方**的名字，不是自己的。
    assert owe["counterpart"] == lender.display_name
    assert owed["counterpart"] == borrower.display_name

    # 同一筆債不會同時出現在自己的兩欄。
    assert _find(mine, "owed_to_me", debt_id) is None
    assert _find(theirs, "i_owe", debt_id) is None

    assert owe["label"] == "☕️ 一杯咖啡 + 一份點心"
    assert owe["days"] == 3


def test_nobody_else_sees_this_debt(pair) -> None:
    """帳本只給當事人看。第三個人打同一支端點不該看到別人的債。"""
    _, _, debt_id = pair
    stranger = User(
        id=uuid.uuid4(), observ_user_id=1, email="x@example.com", display_name="路人"
    )
    seen = _client(stranger).get("/api/ledger").json()
    assert all(_find(seen, s, debt_id) is None for s in seen)


# ── 2. 結清：債主說了算 ─────────────────────────────────────────────


def test_only_the_lender_can_settle(pair) -> None:
    borrower, lender, debt_id = pair

    refused = _client(borrower).post(f"/api/ledger/{debt_id}/settle")
    assert refused.status_code == 403

    done = _client(lender).post(f"/api/ledger/{debt_id}/settle")
    assert done.status_code == 200
    assert done.json()["status"] == "settled"


def test_a_stranger_cannot_settle_someone_elses_debt(pair) -> None:
    _, _, debt_id = pair
    stranger = User(
        id=uuid.uuid4(), observ_user_id=2, email="y@example.com", display_name="路人乙"
    )
    assert _client(stranger).post(f"/api/ledger/{debt_id}/settle").status_code == 403
    assert _client(stranger).post(f"/api/ledger/{debt_id}/nudge").status_code == 403


def test_settled_moves_out_of_both_open_lists(pair) -> None:
    borrower, lender, debt_id = pair
    _client(lender).post(f"/api/ledger/{debt_id}/settle")

    mine = _client(borrower).get("/api/ledger").json()
    theirs = _client(lender).get("/api/ledger").json()

    assert _find(mine, "i_owe", debt_id) is None
    assert _find(theirs, "owed_to_me", debt_id) is None
    assert _find(mine, "settled", debt_id) is not None
    assert _find(theirs, "settled", debt_id) is not None


# ── 3. 「我請過了」：催促的責任在欠債的人身上，但它不是結清 ────────────


def test_only_the_borrower_can_nudge(pair) -> None:
    borrower, lender, debt_id = pair
    assert _client(lender).post(f"/api/ledger/{debt_id}/nudge").status_code == 403
    assert _client(borrower).post(f"/api/ledger/{debt_id}/nudge").status_code == 200


def test_a_nudge_is_not_a_settlement(pair) -> None:
    """戳完債還在 —— 兩邊都還看得到，只是多一句「對方說請過了」。"""
    borrower, lender, debt_id = pair
    _client(borrower).post(f"/api/ledger/{debt_id}/nudge")

    mine = _client(borrower).get("/api/ledger").json()
    theirs = _client(lender).get("/api/ledger").json()

    assert _find(mine, "i_owe", debt_id)["status"] == "nudged"
    assert _find(theirs, "owed_to_me", debt_id)["status"] == "nudged"
    assert _find(mine, "settled", debt_id) is None


def test_the_lender_can_still_settle_after_a_nudge(pair) -> None:
    borrower, lender, debt_id = pair
    _client(borrower).post(f"/api/ledger/{debt_id}/nudge")
    assert _client(lender).post(f"/api/ledger/{debt_id}/settle").status_code == 200


# ── 4. 借用者撤銷不了債主的宣告 ─────────────────────────────────────


def test_the_borrower_cannot_unsettle_by_nudging(pair) -> None:
    """2026-09-23 實測撞到的：戳一筆已結清的債會把它救回來。

    後果不是狀態不漂亮 —— 那筆會重新出現在債主的「別人欠你」，而他剛才明明
    宣告過它結清了，也不會收到任何通知。結清由債主說了算（SPEC §4.8），
    借用者按得動的每一顆按鈕都不該能推翻它。
    """
    borrower, lender, debt_id = pair
    assert _client(lender).post(f"/api/ledger/{debt_id}/settle").status_code == 200

    blocked = _client(borrower).post(f"/api/ledger/{debt_id}/nudge")
    assert blocked.status_code == 409

    theirs = _client(lender).get("/api/ledger").json()
    assert _find(theirs, "settled", debt_id) is not None
    assert _find(theirs, "owed_to_me", debt_id) is None


def test_settling_twice_is_the_same_as_once(pair, sent) -> None:
    """兩個分頁、或 Teams 連結點兩下。不是錯誤，但也不該再送一則通知。"""
    _, lender, debt_id = pair
    assert _client(lender).post(f"/api/ledger/{debt_id}/settle").status_code == 200
    assert _client(lender).post(f"/api/ledger/{debt_id}/settle").status_code == 200
    assert [n for n in sent if n[0] == "settled"] == [("settled", debt_id)]


# ── 5. 兩則通知：沒有它們，兩顆按鈕都是按了沒事發生 ────────────────────


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, uuid.UUID]]:
    """攔下 notify，只記「送了哪一則、給哪一筆」—— 不真的打 Teams。

    用 `monkeypatch` 而不是直接指派：`notify` 是模組層的單例，換掉沒還回去
    的話後面每一個測試都在跟一個假的 notify 相處。
    """
    from app import notify

    log: list[tuple[str, uuid.UUID]] = []
    monkeypatch.setattr(
        notify, "debt_settled", lambda *a: log.append(("settled", a[3]))
    )
    monkeypatch.setattr(notify, "debt_nudged", lambda *a: log.append(("nudged", a[3])))
    return log


def test_settling_tells_the_borrower(pair, sent) -> None:
    """結清是債主單方面宣告的 —— 沒有這則，借用者不主動看就不知道被銷帳了。"""
    _, lender, debt_id = pair
    _client(lender).post(f"/api/ledger/{debt_id}/settle")
    assert ("settled", debt_id) in sent


def test_nudging_tells_the_lender(pair, sent) -> None:
    """**這則就是「我請過了」的全部作用。** 沒有它，債主只能剛好開帳本、
    剛好注意到多一行小字。"""
    borrower, _, debt_id = pair
    _client(borrower).post(f"/api/ledger/{debt_id}/nudge")
    assert ("nudged", debt_id) in sent


def test_a_refused_action_notifies_nobody(pair, sent) -> None:
    """被 403 擋下來的按鈕不該送出通知 —— 那會變成一種戳人的方式。"""
    borrower, lender, debt_id = pair
    _client(borrower).post(f"/api/ledger/{debt_id}/settle")
    _client(lender).post(f"/api/ledger/{debt_id}/nudge")
    assert sent == []


def test_the_nudge_card_really_goes_out_and_points_at_this_debt(
    pair, monkeypatch
) -> None:
    """這一則不攔 `notify`，攔最底下那支 HTTP —— 前面那些只證明端點呼叫了
    `notify`，證明不了真的組得出卡片。而 `notify` 每一支都是「沒設 webhook
    就靜靜 return」，很容易在測試裡一路綠燈卻什麼都沒送。

    順便釘住連結指到**這一筆**：web-spec §6 要的「直接按」做不到（Workflows
    不渲染卡片按鈕），退到的版本就是這個連結，它指錯就等於沒做。
    """
    from app import notify

    borrower, lender, debt_id = pair
    posted: list[tuple[str, dict]] = []

    async def capture(url: str, payload: dict) -> None:
        posted.append((url, payload))

    monkeypatch.setattr(notify, "_post", capture)
    _set_webhook(lender.id, "https://example.invalid/hook")

    assert _client(borrower).post(f"/api/ledger/{debt_id}/nudge").status_code == 200

    assert posted, "債主設了私訊 webhook，戳他就該真的送出一張卡片"
    _, card = posted[0]
    assert str(debt_id) in card["text"], "連結要指到這一筆，不是帳本首頁"
    assert borrower.display_name in card["text"]


def test_no_personal_webhook_falls_back_to_the_channel(pair, monkeypatch) -> None:
    """沒設個人通知的人一律退到頻道 —— 結清與戳兩則都一樣。

    這兩則都是**單方面宣告**：宣告的人已經做完他那一半，另一半只有在對方
    真的收到時才存在。「他自己去開網頁」不是一個退路，是讓那半邊隨機發生。
    """
    from app import notify

    borrower, lender, debt_id = pair
    posted: list[tuple[str, dict]] = []

    async def capture(url: str, payload: dict) -> None:
        posted.append((url, payload))

    monkeypatch.setattr(notify, "_post", capture)
    monkeypatch.setattr(notify.settings, "teams_channel_webhook", CHANNEL)
    # 兩個人都沒設個人 webhook —— `pair` 建出來的就是這樣，站上多數人也是。

    assert _client(borrower).post(f"/api/ledger/{debt_id}/nudge").status_code == 200
    assert _client(lender).post(f"/api/ledger/{debt_id}/settle").status_code == 200

    assert [url for url, _ in posted] == [CHANNEL, CHANNEL]
    # @mention 要指到當事人：戳的那則點債主，銷帳那則點借用者。
    assert _mentioned(posted[0][1]) == lender.email
    assert _mentioned(posted[1][1]) == borrower.email


CHANNEL = "https://example.invalid/channel"


def _mentioned(payload: dict) -> str:
    card = payload["attachments"][0]["content"]
    return card["msteams"]["entities"][0]["mentioned"]["id"]


def _set_webhook(user_id: uuid.UUID, url: str) -> None:
    async def go(s):
        user = await s.get(User, user_id)
        user.teams_webhook_url = url
        await s.commit()

    _run(go)


def test_a_missing_debt_is_a_404(pair) -> None:
    borrower, _, _ = pair
    gone = uuid.uuid4()
    assert _client(borrower).post(f"/api/ledger/{gone}/settle").status_code == 404
