"""站台管理者的維運介面。

**這一頁的分界：看得到什麼壞了，不能瀏覽誰欠誰。**

`_get_job()` 刻意限制「誰能看到誰的 job」，而在一個以人情債為核心的產品裡，
「誰用得兇、誰欠誰多少」是社交敏感資訊 —— 那不是技術問題，是這個工具會不會
被同事信任的問題。所以這裡只有系統健康、**不指名的**聚合數字，以及卡住的 job。
沒有全站 job 瀏覽，也沒有任何 job 內容（security.md 紅線 1）。

🚨 **每一個綠燈都要是真的量到的。** 量不到的東西放在 `unknown` 裡並寫出原因，
不要放一顆永遠綠的燈 —— 那比沒有指示器危險，因為它會被相信。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import is_admin, require_user
from ..config import settings
from ..db import get_session
from ..enums import JobStatus
from ..fxrate import usd_to_twd
from ..models import Job, LendingAccount, LendingSetting, User, WorkerHost
from .workers import _fresh_utilization, _light, host_online

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 「卡住」的判定寫死在這裡並附理由，不要每個人自己抓一個數字。
#
# 排隊 10 分鐘還沒被領走就算卡住 —— 站台在 15 分鐘（`job_queue_expiry_seconds`）
# 會把它作廢，所以這個門檻讓管理者有五分鐘可以反應。改其中一個要看另一個。
QUEUED_STUCK_SECONDS = 600
# 執行中超過這個時間算卡住。worker 的預設逾時是 600 秒（worker/worker.py），
# 加兩分鐘寬限給收尾與上傳。
#
# 2026-09-23 起這份清單只剩「worker 還在回報、但跑太久」的那種：worker 已經沒回報
# 的 job 會被 app/orphans.py 在 job_heartbeat_timeout_seconds 之後自動結案為 failed，
# 不會留在這裡等人。
#
# ⚠️ **worker 沒有把自己的逾時回報給 hub**（`WorkerConfig` 只有 network／models／
# budget／concurrency／CLI 版本），所以這是一個假設，不是量到的值。有代跑者把
# 逾時調長的話，他的 job 會提早被列進來。要精確就得讓 worker 回報那個值。
RUNNING_STUCK_SECONDS = 600 + 120
# 帶 zip 的 job 在 worker 那邊拿到的是 1800 秒（`worker.timeout_for_inputs`）——
# 用短的那條線會讓每個健康的 code job 從第 12 分鐘列到第 30 分鐘，而這個面板的
# 用途是「一眼看出哪個永遠不會結束」，被例行事件塞滿之後它就不再被人看了。
#
# ⚠️ **1800 這個數字在 worker 與這裡各有一份，它們必須一致。** 同上面那格的理由：
# worker 不回報自己的逾時。要消掉這份重複，就得讓 `WorkerConfig` 帶上它。
ZIP_RUNNING_STUCK_SECONDS = 1800 + 120
_ZIP_SUFFIX = ".zip"


def running_stuck_seconds(attachment_keys: list[str]) -> int:
    """這個 job 跑多久算卡住。條件與 worker 給長逾時的條件相同：附件裡有 zip。"""
    if any(key.lower().endswith(_ZIP_SUFFIX) for key in attachment_keys):
        return ZIP_RUNNING_STUCK_SECONDS
    return RUNNING_STUCK_SECONDS


# 一項檢查的兩種「位址」，刻意分開（web-spec §8 之外的維運頁，但同一條精神）：
#
#   target    hub **這次真的去打的**那個位址。純文字，不做成連結 ——
#             它常常是 localhost:9000，而管理者的瀏覽器點下去是他自己的機器。
#   open_url  人點得開的介面。只有真的存在時才有（MinIO console、Observ）。
#
# 兩者指向不同機器是常態，不是設定錯誤。合成一欄的話，`localhost:9000` 會被
# 當成 console 的網址點下去，然後得到一個「連不上」的結論 —— 而那個結論是錯的。


def _db_target() -> str:
    """資料庫位址，**去掉帳號與密碼**。

    這一頁只有管理者看得到，但它仍然是一個 API 回應 —— security.md 那條
    「絕不把憑證放進錯誤訊息與 API 回應」沒有例外。

    也不做遮罩版（`boba:***@…`）：遮罩要自己寫，而寫錯一次就是把密碼吐出去。
    那條規則不該靠一段字串處理來守，而「用哪個帳號連的」在這頁沒有人會拿它
    做決定 —— 帳號錯的話燈本來就是紅的，錯誤訊息會在 detail 裡。
    """
    try:
        u = make_url(settings.database_url)
    except Exception:  # noqa: BLE001 —— 位址壞掉不該讓整頁 500
        return "（讀不出來）"
    host = u.host or "?"
    return f"{host}:{u.port}/{u.database}" if u.port else f"{host}/{u.database}"


async def require_admin(user: User = Depends(require_user)) -> User:
    """每一支都擋在後端，不是靠前端不顯示。

    前端不渲染不等於沒送出去 —— 打開開發者工具就繞過去了。
    """
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="這一頁只有站台管理者看得到")
    return user


@router.get("/health")
async def health(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict:
    """各元件的狀態。三種分類刻意分開，因為它們的可信度不同：

    - `checks`：**這次真的去問過**，綠燈代表剛剛量到的事實
    - `facts`：讀得出來但不是健康檢查（例如 schema 版本）
    - `unknown`：從 hub 檢查不到的東西，連同原因一起講
    """
    checks: list[dict] = []

    # 1. 資料庫：真的跑一個 query。
    t0 = time.monotonic()
    try:
        await session.scalar(select(1))
        # 本機的 SELECT 1 常常不到 1 毫秒，取整數會印出「0 ms」——
        # 那是真的，但在健康面板上讀起來像「沒量到」，而可信是這頁唯一的資產。
        ms = (time.monotonic() - t0) * 1000
        checks.append(
            {
                "key": "db",
                "label": "資料庫",
                "ok": True,
                "detail": f"查詢往返 {'< 0.1' if ms < 0.1 else f'{ms:.1f}'} ms",
                "target": _db_target(),
            }
        )
    except Exception as exc:  # noqa: BLE001 —— 這裡就是要把任何失敗變成紅燈
        checks.append(
            {
                "key": "db",
                "label": "資料庫",
                "ok": False,
                "detail": str(exc)[:200],
                "target": _db_target(),
            }
        )

    # 2. MinIO：打它自己的健康端點。不要用「我們存得進去嗎」當檢查 ——
    #    那會在正常運作時寫入垃圾物件。
    url = settings.s3_endpoint.rstrip("/") + "/minio/health/live"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(url)
        checks.append(
            {
                "key": "storage",
                "label": "檔案儲存（MinIO）",
                "ok": r.status_code == 200,
                # 位址以前是寫在這句話裡的。拆出來成獨立欄位之後，前端不用
                # 解析後端的散文 —— 那是最脆的那種耦合。
                "detail": f"健康端點回 {r.status_code}",
                "target": url,
                "open_url": settings.s3_console_url,
            }
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            {
                "key": "storage",
                "label": "檔案儲存（MinIO）",
                "ok": False,
                "detail": f"連不上：{str(exc)[:120]}",
                "target": url,
                "open_url": settings.s3_console_url,
            }
        )

    # 3. 領單主機：last_seen_at 是它真的回報過才會動的。
    #    託管模型下只有一台，它沒跑起來就等於沒有人的額度借得出去。
    up = await host_online(session)
    seen = await session.scalar(select(func.max(WorkerHost.last_seen_at)))
    checks.append(
        {
            "key": "worker_host",
            "label": "領單主機",
            "ok": up,
            # 沒有位址，而且**不可能有**：worker 一律主動連出（可能在 NAT 後），
            # hub 從不主動連它（SPEC §9）。留一格空白會被讀成「這裡壞了」。
            "target_note": "它主動連 hub，hub 不連它 —— 沒有可以檢查的位址",
            "detail": (
                f"最後回報於 {int((datetime.now(UTC) - seen).total_seconds())} 秒前"
                if seen
                else "從來沒有回報過"
            ),
        }
    )

    # 4. 出借帳號：能跑的有幾個、幾個等著重新授權。
    #    一個帳號的 token 失效只會停掉那個帳號，其他照跑（web-spec §8）——
    #    所以它不會有任何 job 失敗，也就不會有人發現，除非這裡講。
    accounts = list(await session.scalars(select(LendingAccount)))
    usable = [a for a in accounts if a.usable]
    stale = [a for a in accounts if a.needs_reauth]
    checks.append(
        {
            "key": "lending_accounts",
            "label": "出借帳號",
            "ok": bool(usable),
            "target_note": "量的是站台自己的資料，不是遠端服務",
            "detail": (
                f"{len(usable)} 個可用"
                + (f"，{len(stale)} 個等待重新授權" if stale else "")
            ),
        }
    )

    # 5. 認證（Observ）：站台唯一的登入路徑。它掛了就沒有人進得來，
    #    而在這之前這一頁會整排全綠 —— 那是最糟的一種沉默。
    #
    # 🚨 **不要用 `/observ/health`。** 它回 200，但那是前端 SPA 的 catch-all，
    #    不是健康端點 —— `/observ/隨便打什麼` 也回 200。拿它當檢查會做出一顆
    #    永遠綠的假燈，而這一頁的第一條規則就是在禁止那個。
    #
    # `/api/healthz/` 才是真的：OpenAPI 上它的 security 是 `[jwtAuth, {}]`，
    # 那個空物件代表允許匿名，回的是 JSON 版本資訊（2026-09-22 在 production
    # 實測）。**所以綠燈的條件是「200 而且 JSON 解得開」** —— 只看狀態碼的話，
    # 路由哪天掉進 SPA 兜底，這顆燈會在對方掛掉時繼續是綠的。
    observ_url = settings.observ_base_url.rstrip("/") + "/api/healthz/"
    check = {
        "key": "auth",
        "label": "認證（Observ）",
        "target": observ_url,
        "open_url": settings.observ_base_url,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(observ_url)
        body = r.json() if r.status_code == 200 else None
    except ValueError:
        # 200 但不是 JSON = 打到 SPA 了。這是紅燈，而且原因要講得出來，
        # 否則下一個人會以為 Observ 掛了，然後去查一個沒壞的東西。
        checks.append(
            check
            | {
                "ok": False,
                "detail": "回了 200 但不是 JSON —— 打到前端頁面，健康端點的路徑可能變了",
            }
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(check | {"ok": False, "detail": f"連不上：{str(exc)[:120]}"})
    else:
        if body is None:
            checks.append(check | {"ok": False, "detail": f"回 {r.status_code}"})
        else:
            # 版本帶出來：它回答「我在跟哪一版的 Observ 講話」，那是對方改 API
            # 時第一個要問的事。順帶讓這顆燈看得出來有在動 —— 寫死的綠燈不會
            # 帶著一個會變的 commit。
            ver = " ".join(str(body[k]) for k in ("branch", "commit") if body.get(k))
            checks.append(
                check
                | {
                    "ok": True,
                    "detail": f"認證 API 有回應{f'（{ver}）' if ver else ''}"
                    " —— 這顆燈只證明它回得了話，不代表登入一定成功",
                }
            )

    facts = [
        {
            "label": "資料庫 schema 版本",
            "value": await _schema_revision(session),
            # 這個**不放成綠燈**：hub 啟動時就檢查，版本不符會拒絕啟動，
            # 所以它在畫面上永遠會是綠的 —— 一顆永遠綠的燈沒有資訊量。
            "note": "hub 啟動時就檢查，落後會直接拒絕啟動",
        }
    ]

    unknown = [
        {
            "label": "job 容器的對外白名單（egress proxy）",
            "why": (
                "它是代跑者機器上的 docker 容器，hub 連不到它，"
                "也沒有任何回報管道 —— 這一項無法從 hub 確認"
            ),
        }
    ]

    return {"checks": checks, "facts": facts, "unknown": unknown}


async def _schema_revision(session: AsyncSession) -> str:
    """alembic 自己的版本表。讀不到就講讀不到，不要回一個假的版本字串。"""
    try:
        return (
            await session.scalar(text("SELECT version_num FROM alembic_version"))
        ) or "（未初始化）"
    except Exception:  # noqa: BLE001
        return "（讀不到）"


@router.get("/approved-accounts")
async def approved_accounts(
    days: int = 30,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """有具名批准者的出借帳號，以及它們最近跑了多少 job。

    **這一支是 ADR-0001 的條件，不是裝飾。** 派單挑 utilization 低的帳號，
    公司帳號因此是穩定的曝險而不是偶爾的溢出。批准者的名字留了痕跡、用量卻
    沒有人看得到，等於只留了一半 —— 被問起「誰放的、跑了多少」時只答得出一半。

    **不含任何 job 內容**（紅線 1），也不指名委託者 —— 只有帳號、批准者、筆數與金額。
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await session.execute(
        select(
            LendingAccount.id,
            LendingAccount.name,
            LendingAccount.approver_note,
            User.display_name,
            func.count(Job.id),
            func.coalesce(func.sum(Job.total_cost_usd), 0),
        )
        .join(LendingSetting, LendingAccount.lending_id == LendingSetting.id)
        .join(User, LendingSetting.owner_user_id == User.id)
        .join(
            Job,
            (Job.account_id == LendingAccount.id) & (Job.created_at >= since),
            isouter=True,
        )
        .where(LendingAccount.approver_note.is_not(None))
        .group_by(
            LendingAccount.id,
            LendingAccount.name,
            LendingAccount.approver_note,
            User.display_name,
        )
        .order_by(func.count(Job.id).desc())
    )
    return [
        {
            "account_id": str(aid),
            "name": name,
            "approver_note": note,
            "lender": lender,
            "days": days,
            "job_count": n,
            "cost_usd": str(cost),
        }
        for aid, name, note, lender, n, cost in rows
    ]


@router.get("/lending-accounts")
async def lending_accounts(
    days: int = 30,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """站台上所有出借帳號，維運視角（2026-09-24，CONTEXT.md「出借帳號」）。

    回答三件事，照這個順序：哪個壞了（等重新授權）、誰放的（批准者，ADR-0001）、
    有幾個（代跑者、方案、最近有沒有被派到）。

    **給的是身分與狀態，不給用量百分比**：燈號跟委託者在下拉看到的同一級。
    百分比是代跑者自己的事，管理者拿到它沒有維運用途（web-spec §3 的理由同樣適用）。
    **不含 job 內容、不指名委託者**（紅線 1）。已停用的（按過「不再出借」、
    紀錄留著因為跑過 job）也列，排最後 —— 查「上個月那筆是哪個帳號跑的」時要在。
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                LendingAccount,
                User.display_name,
                func.count(Job.id),
                func.coalesce(func.sum(Job.total_cost_usd), 0),
            )
            .join(LendingSetting, LendingAccount.lending_id == LendingSetting.id)
            .join(User, LendingSetting.owner_user_id == User.id)
            .join(
                Job,
                (Job.account_id == LendingAccount.id) & (Job.created_at >= since),
                isouter=True,
            )
            .group_by(LendingAccount.id, User.display_name)
        )
    ).all()

    def status_of(a: LendingAccount) -> str:
        if a.needs_reauth:
            return "needs_reauth"
        if a.usable:
            return "usable"
        return "retired"

    order = {"needs_reauth": 0, "usable": 1, "retired": 2}
    out = [
        {
            "account_id": str(a.id),
            "name": a.name,
            "lender": lender,
            "status": status_of(a),
            "claude_email": a.claude_email,
            "claude_plan": a.claude_plan,
            "quota": _light(_fresh_utilization(a)) if a.usable else "unknown",
            "last_assigned_at": (
                a.last_assigned_at.isoformat() if a.last_assigned_at else None
            ),
            "credits_required_models": list(a.credits_required_models or []),
            "approver_note": a.approver_note,
            "days": days,
            "jobs": int(n),
            "cost_usd": str(cost),
        }
        for a, lender, n, cost in rows
    ]
    out.sort(
        key=lambda r: (
            order[r["status"]],
            r["lender"],
            r["last_assigned_at"] or "",
        )
    )
    return out


@router.get("/stats")
async def stats(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict:
    """聚合數字，**不指名**。

    「誰用得兇」「誰欠誰」不放這裡 —— 見模組開頭。所以這裡只有總數，
    沒有任何 per-user 的拆解。
    """
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    rows = await session.execute(select(Job.status, func.count()).group_by(Job.status))
    by_status = {str(s): n for s, n in rows}
    done = sum(by_status.get(s, 0) for s in (JobStatus.SUCCEEDED, JobStatus.FAILED))
    spend = await session.scalar(select(func.sum(Job.total_cost_usd))) or Decimal(0)
    rate, note = await usd_to_twd()
    return {
        "users": users,
        "jobs_total": sum(by_status.values()),
        "jobs_by_status": by_status,
        # 成功率的分母只算跑完的（成功 + 失敗）—— 把排隊中的算進去的話，
        # 剛丟一批 job 進來就會讓成功率看起來掉下去。
        "success_rate": (
            round(by_status.get(str(JobStatus.SUCCEEDED), 0) / done, 3)
            if done
            else None
        ),
        "spend_usd": str(spend),
        # 台幣是**粗估**，而且是附註 —— 帳單是美金，對帳以美金為準。
        # 拿不到匯率就回 None 並把原因一起回（`twd_note`），不要回一個猜的數字：
        # 一個看起來像量到、其實是寫死係數的台幣，比沒有台幣糟。
        "twd": (
            {
                "rate": str(rate.twd_per_usd),
                "quoted_on": rate.quoted_on,
                "source": rate.source_label,
            }
            if rate
            else None
        ),
        "twd_note": note,
    }


@router.get("/stuck-jobs")
async def stuck_jobs(
    _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """只列異常的 job，不是全站瀏覽。

    **不含任何 job 內容**（紅線 1）—— 只有 id、狀態、卡多久、哪位代跑者。
    """
    now = datetime.now(UTC)
    queued_cutoff = now - timedelta(seconds=QUEUED_STUCK_SECONDS)
    # SQL 用短的那條線撈候選，帶 zip 的再於 Python 端剔掉 —— 門檻是每個 job
    # 各自的，塞不進一個 WHERE 子句。
    running_cutoff = now - timedelta(seconds=RUNNING_STUCK_SECONDS)

    rows = await session.execute(
        select(Job, User.display_name)
        .join(LendingSetting, Job.lending_id == LendingSetting.id, isouter=True)
        .join(User, LendingSetting.owner_user_id == User.id, isouter=True)
        .where(
            ((Job.status == JobStatus.QUEUED) & (Job.created_at < queued_cutoff))
            | (
                Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING])
                & (func.coalesce(Job.started_at, Job.claimed_at) < running_cutoff)
            )
        )
        .order_by(Job.created_at)
        .limit(50)
    )
    out = []
    for job, lender_name in rows:
        since = job.started_at or job.claimed_at or job.created_at
        stuck_for = (now - since).total_seconds()
        if job.status in (JobStatus.CLAIMED, JobStatus.RUNNING) and stuck_for < (
            running_stuck_seconds(job.attachment_keys or [])
        ):
            continue
        out.append(
            {
                "id": str(job.id),
                "status": str(job.status),
                "stuck_seconds": int(stuck_for),
                "lender": lender_name,
            }
        )
    return out
