"""整個測試套件的兩道防護。都是 2026-09-23 被真實事件逼出來的。

這個 repo 的測試**打的是 `DATABASE_URL` 指到的真資料庫**，沒有測試專用的
資料庫。那是知情的取捨（少一套 fixture 基礎設施），但它有兩個後果，這裡各擋一個：

1. **有人載著 production 的 `.env` 跑 pytest，正式站就會多出一堆假人。**
   → 資料庫名字不是 `boba` 就拒絕啟動，在第一個測試之前就爆。

2. **fixture 沒收乾淨的假資料會留在開發資料庫裡**，然後出現在派單下拉選單與
   排行榜上。實際發生過：108 個 `債主xxxxxxxx` 塞滿了提交頁的代跑者選單。
   → session 結束時掃掉**這一次執行期間**建立的 `@example.com` 假人。

第 2 道刻意只掃這次建立的，不掃歷史上的：測試套件不該刪它沒建的東西。
歷史遺留的要另外清（見 SPEC.md §11 的紀錄）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest
from app.config import settings
from app.db import SessionLocal, engine
from app.models import Debt, Job, LendingAccount, LendingSetting, User
from sqlalchemy import delete, func, select

ALLOWED_DB_NAMES = {"boba"}
FIXTURE_EMAIL_SUFFIX = "@example.com"


@pytest.fixture(scope="session", autouse=True)
def _observ_placeholder():
    """認證來源給一組假值，讓 app 的 lifespan 起得來。

    2026-09-23 起 `OBSERV_BASE_URL` / `OBSERV_SERVICE_ID` 沒設就拒絕啟動
    （main.py `_check_observ`）—— 那是刻意的 fail closed，而測試環境本來就
    沒有這兩個值。這裡補上假的，**不是**把那道檢查關掉：它自己有測試
    （test_observ_is_required.py），而且任何真的會打出去的測試都要自己 monkeypatch。
    """
    settings.observ_base_url = settings.observ_base_url or "https://observ.example.com"
    settings.observ_service_id = settings.observ_service_id or "test-service-id"


def _db_name(url: str) -> str:
    return urlparse(url).path.lstrip("/")


@pytest.fixture(scope="session", autouse=True)
def _refuse_to_test_against_the_wrong_database():
    """資料庫名字不是開發用的那個，整個套件直接不跑。

    這不是 pytest 的 skip —— skip 會綠，而綠燈正是要避免的：
    「測試過了」不能跟「差一步就把 production 塞滿假人」長得一樣。
    """
    name = _db_name(settings.database_url)
    if name not in ALLOWED_DB_NAMES:
        pytest.exit(
            f"拒絕對資料庫「{name}」跑測試（DATABASE_URL={settings.database_url!r}）。"
            f" 測試套件會在真資料庫上建假人，只允許對 {sorted(ALLOWED_DB_NAMES)} 跑。"
            " 這台機器上有 production 的 .env 嗎？",
            returncode=3,
        )
    yield


@pytest.fixture(scope="session", autouse=True)
def _sweep_fixture_users_created_this_session():
    """掃掉這一次執行期間建立的假人。只碰 `@example.com` 且建立時間在 session
    開始之後的；有 job 或 debt 指著的**不碰**，留給人判斷。"""
    started = datetime.now(UTC)
    yield

    async def sweep() -> None:
        engine.sync_engine.pool.dispose()
        async with SessionLocal() as s:
            ids = set(
                await s.scalars(
                    select(User.id).where(
                        User.email.like(f"%{FIXTURE_EMAIL_SUFFIX}"),
                        User.created_at >= started,
                    )
                )
            )
            if not ids:
                return
            lendings = set(
                await s.scalars(
                    select(LendingSetting.id).where(
                        LendingSetting.owner_user_id.in_(ids)
                    )
                )
            )
            referenced = await s.scalar(
                select(func.count())
                .select_from(Job)
                .where(Job.borrower_id.in_(ids) | Job.lending_id.in_(lendings))
            ) + await s.scalar(
                select(func.count())
                .select_from(Debt)
                .where(Debt.borrower_id.in_(ids) | Debt.lender_id.in_(ids))
            )
            if referenced:
                # 有 fixture 沒收乾淨到連 job/debt 都留著。這裡不動，讓人看得到。
                print(
                    f"\n[conftest] {len(ids)} 個假人有 {referenced} 筆 job/debt 指著，"
                    "沒有掃。哪個 fixture 的 teardown 沒跑完？"
                )
                return
            if lendings:
                await s.execute(
                    delete(LendingAccount).where(
                        LendingAccount.lending_id.in_(lendings)
                    )
                )
                await s.execute(
                    delete(LendingSetting).where(LendingSetting.id.in_(lendings))
                )
            await s.execute(delete(User).where(User.id.in_(ids)))
            await s.commit()
            print(f"\n[conftest] 掃掉 {len(ids)} 個這次執行留下的假人")

    asyncio.run(sweep())
