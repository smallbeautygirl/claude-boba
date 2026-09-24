"""管理頁的端點：**每一支都要擋非 admin**，而且分界不能漂移。

擋在後端不是為了整齊 —— 前端不渲染不等於沒送出去，打開開發者工具就繞過去了。

另一半是分界：這頁看得到什麼壞了，**不能瀏覽誰欠誰**。所以聚合數字不指名、
卡住清單不含 job 內容（security.md 紅線 1）。那條界線用測試釘住，
因為它不是技術限制 —— 是這個工具會不會被同事信任的問題，很容易被「順手多回
一個欄位」侵蝕。
"""

from __future__ import annotations

import uuid

import pytest
from app.auth import require_user
from app.config import settings
from app.db import engine
from app.main import app
from app.models import User
from app.routers.admin import QUEUED_STUCK_SECONDS, RUNNING_STUCK_SECONDS
from fastapi.testclient import TestClient

ADMIN = "boss@example.com"
ENDPOINTS = [
    "/api/admin/health",
    "/api/admin/stats",
    "/api/admin/stuck-jobs",
    "/api/admin/lending-accounts",
]


def _client(email: str) -> TestClient:
    app.dependency_overrides[require_user] = lambda: User(
        id=uuid.uuid4(), observ_user_id=1, email=email, display_name="誰"
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _admins(monkeypatch):
    # 每個 TestClient 都跑在自己的事件迴圈上，而 app.db.engine 的連線池會留著
    # 上一個迴圈的連線 —— 不倒掉的話，第二個碰資料庫的測試會拿到
    # 「attached to a different loop」。單獨跑會過、整檔跑會炸，就是這個。
    engine.sync_engine.pool.dispose()
    monkeypatch.setattr(settings, "admin_emails", f" {ADMIN.upper()} ,x@y.com")
    # /stats 會去問匯率。關掉來源，測試才不會在跑的時候打外部服務 ——
    # 那會讓測試在沒網路的機器上變慢、變紅，而且紅的原因跟這個檔在測的事無關。
    monkeypatch.setattr(settings, "fx_api_url", "")
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_refuses_a_normal_user(path) -> None:
    r = _client("someone@example.com").get(path)
    assert r.status_code == 403
    # 訊息不要暗示「你可以怎麼變成 admin」，只說這頁不是給你的。
    assert "管理者" in r.json()["detail"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_admin_gets_in_despite_case_and_spaces(path) -> None:
    """設定裡是大寫加空白、登入的是小寫 —— 人手維護的清單一定長這樣。"""
    assert _client(ADMIN).get(path).status_code == 200


def test_nobody_is_admin_when_the_list_is_empty(monkeypatch) -> None:
    """沒設 = 沒有人是 admin。這是刻意的 fail closed，不是還沒做完。"""
    monkeypatch.setattr(settings, "admin_emails", "")
    assert _client(ADMIN).get("/api/admin/health").status_code == 403


def test_stats_never_names_anyone() -> None:
    """聚合數字不指名。多回一個 per-user 欄位就會踩到這條。"""
    body = _client(ADMIN).get("/api/admin/stats").json()
    # twd / twd_note 是匯率，不是某個人的數字 —— 它們不碰這條界線。
    assert set(body) == {
        "users",
        "jobs_total",
        "jobs_by_status",
        "success_rate",
        "spend_usd",
        "twd",
        "twd_note",
    }


def test_stats_still_answers_when_the_rate_source_is_off() -> None:
    """匯率是附註，不是這一頁的必要條件。

    台幣拿不到就整頁 500 的話，一個「順便」的欄位會有本事讓維運介面在
    最需要它的時候消失。所以那時是 twd=None + 一句原因，其餘照常。
    """
    body = _client(ADMIN).get("/api/admin/stats").json()
    assert body["twd"] is None
    assert body["twd_note"]
    assert body["spend_usd"] is not None


def test_stuck_jobs_carry_no_job_content() -> None:
    """紅線 1：job 內容一律不進這裡 —— prompt、結果、錯誤細節都不行。"""
    rows = _client(ADMIN).get("/api/admin/stuck-jobs").json()
    assert isinstance(rows, list)
    for row in rows:
        assert set(row) == {"id", "status", "stuck_seconds", "worker"}


def test_lending_accounts_carry_identity_and_status_only() -> None:
    """管理者看出借帳號是維運視角（CONTEXT.md）：身分、狀態、批准者、最近用量筆數。

    **沒有用量百分比**（那是代跑者自己的事，燈號跟委託者同一級）、沒有 job 內容、
    不指名委託者（紅線 1）。欄位集合釘死：多回一個欄位就是侵蝕這條線。
    """
    rows = _client(ADMIN).get("/api/admin/lending-accounts").json()
    assert isinstance(rows, list)
    allowed = {
        "account_id",
        "name",
        "lender",
        "status",
        "claude_email",
        "claude_plan",
        "quota",
        "last_assigned_at",
        "credits_required_models",
        "approver_note",
        "days",
        "jobs",
        "cost_usd",
    }
    for row in rows:
        assert set(row) == allowed
        assert row["status"] in {"needs_reauth", "usable", "retired"}
        assert row["quota"] in {"green", "yellow", "red", "unknown"}
    # 等重新授權的在最前面，已停用的在最後。
    order = [{"needs_reauth": 0, "usable": 1, "retired": 2}[r["status"]] for r in rows]
    assert order == sorted(order)


def test_health_separates_measured_from_unknown() -> None:
    """量到的、讀到的、量不到的要分開。

    egress proxy 必須留在 `unknown` 且附原因 —— 把它做成一顆永遠綠的燈，
    比完全沒有這一項危險，因為它會被相信。
    """
    body = _client(ADMIN).get("/api/admin/health").json()
    assert {"checks", "facts", "unknown"} == set(body)
    assert all({"key", "label", "ok", "detail"} <= set(c) for c in body["checks"])
    assert any("egress" in u["label"] for u in body["unknown"])
    assert all(u["why"] for u in body["unknown"])


def test_stuck_thresholds_leave_room_before_the_job_expires() -> None:
    """排隊的門檻要早於站台把 job 作廢的時間，不然管理者看到時已經來不及。"""
    assert QUEUED_STUCK_SECONDS < settings.job_queue_expiry_seconds
    assert RUNNING_STUCK_SECONDS > 600  # worker 預設逾時，外加寬限


# —— 卡住的 job 真的抓得到嗎 ——
#
# 上面那支端點在正常的站台上回空陣列，而「空」跟「偵測不到」在畫面上長得一樣。
# 所以這裡真的塞一筆逾時的 job 進資料庫（交易包住、結束 rollback）再問一次。


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db():
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except (OperationalError, OSError) as exc:
        await eng.dispose()
        pytest.skip(f"連不到資料庫：{exc}")
    trans = await conn.begin()
    async with AsyncSession(bind=conn, expire_on_commit=False) as s:
        yield s
    await trans.rollback()
    await conn.close()
    await eng.dispose()


@pytest.mark.anyio
async def test_a_long_queued_job_shows_up(db) -> None:
    from datetime import UTC, datetime, timedelta

    from app.enums import JobStatus, SourceType
    from app.models import Job
    from app.routers.admin import stuck_jobs

    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="委託者",
    )
    db.add(user)
    await db.flush()
    old = datetime.now(UTC) - timedelta(seconds=QUEUED_STUCK_SECONDS + 60)
    job = Job(
        borrower_id=user.id,
        source_type=SourceType.PASTE,
        prompt="這句不可以出現在回應裡",
        status=JobStatus.QUEUED,
        created_at=old,
    )
    db.add(job)
    await db.flush()

    rows = await stuck_jobs(user, db)
    mine = [r for r in rows if r["id"] == str(job.id)]
    assert mine, "排隊超過門檻的 job 沒有被列出來"
    assert mine[0]["stuck_seconds"] >= QUEUED_STUCK_SECONDS
    # 紅線 1：內容一個字都不能跟著出來。
    assert "prompt" not in mine[0]
    assert all("這句不可以出現在回應裡" not in str(v) for v in mine[0].values())


# --- 位址欄位與 Observ 那顆燈 ----------------------------------------------
#
# 這一段釘的是 2026-09-22 的決定：狀態列每一項要說得出「hub 剛剛去打的是誰」。


def test_db_target_never_carries_the_password() -> None:
    """資料庫位址只有 host:port/dbname。

    這頁只有管理者看得到，但它仍然是一個 API 回應 —— security.md 那條
    「絕不把憑證放進 API 回應」沒有例外。斷言寫成「整串裡不能有密碼」而不是
    「不要有 @」：後者擋不住換一種格式寫出來的同一個外洩。
    """
    from app.routers.admin import _db_target

    old = settings.database_url
    settings.database_url = "postgresql+asyncpg://boba:hunter2@db.example:5432/boba"
    try:
        target = _db_target()
    finally:
        settings.database_url = old

    assert "hunter2" not in target
    assert "boba:" not in target
    assert target == "db.example:5432/boba"


def test_a_broken_database_url_does_not_500_the_page() -> None:
    """位址壞掉不該讓整頁掛掉 —— 那會讓管理者在最需要這頁的時候看不到它。"""
    from app.routers.admin import _db_target

    old = settings.database_url
    settings.database_url = "這不是一個網址"
    try:
        assert _db_target() == "（讀不出來）"
    finally:
        settings.database_url = old


def test_observ_200_that_is_not_json_is_a_red_light(monkeypatch) -> None:
    """🚨 **200 不等於活著。**

    `/observ/health` 與 `/observ/隨便打什麼` 都回 200，因為那是前端 SPA 的
    catch-all。只看狀態碼的檢查會在 API 掛掉、SPA 還活著時繼續是綠的 ——
    而那正是最需要它變紅的時刻。

    這個測試存在的理由就是那顆假燈：判準改回「只看 2xx」時它要炸。
    """
    import httpx

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, _url):
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: _Client())
    with _client(ADMIN) as c:
        body = c.get("/api/admin/health").json()

    auth = next(x for x in body["checks"] if x["key"] == "auth")
    assert auth["ok"] is False
    assert "不是 JSON" in auth["detail"]
    # 紅了也要說得出打的是誰 —— 否則下一個人會去查一個沒壞的東西。
    assert auth["target"].endswith("/api/healthz/")


def test_checks_without_an_address_say_why(monkeypatch) -> None:
    """留一格空白會被讀成「這裡本來該有東西，是不是壞了」。

    這頁已經有一個 `unknown` 區塊，理由一模一樣：量不到的東西要寫出原因，
    不要留白，也不要放一個看起來像位址的替代品（主機名不是位址）。
    """
    with _client(ADMIN) as c:
        body = c.get("/api/admin/health").json()

    for check in body["checks"]:
        assert check.get("target") or check.get("target_note"), check["key"]
