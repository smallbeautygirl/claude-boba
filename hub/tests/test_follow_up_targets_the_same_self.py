"""接著問：上一輪是你自己跑的，這一輪也要派給你自己。

2026-09-23 正式站：站台上只有一位代跑者（就是委託者本人），第一輪指定自己、
跑成功；按「接著問」，新 job 排隊 15 分鐘後被作廢，畫面說「目前沒人有空」——
而下拉裡明明有一位在線的代跑者，就是他自己。

原因：follow_up() 把 requested_lending_id 寫死成 None（「自動」），而「自動」
自 2026-09-23 起不派給本人（SPEC §4.5）。兩條各自合理的規則接在一起就是一條死路，
而且是安靜的那種：提交時沒擋，排隊到過期才告訴他。

這裡跑真的資料庫，交易包住、結束 rollback（同 test_queue_dead_ends.py）。
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import Job, JobStatus, LendingAccount, LendingSetting, SourceType, User
from app.routers.jobs import _check_model, follow_up
from app.schemas import FollowUp
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session():
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


async def _self_lender(
    session: AsyncSession,
) -> tuple[User, LendingSetting, LendingAccount]:
    user = User(
        observ_user_id=uuid.uuid4().int % 2_000_000_000,
        email=f"{uuid.uuid4()}@example.com",
        display_name="自己跑自己的人",
    )
    session.add(user)
    await session.flush()
    setting = LendingSetting(
        owner_user_id=user.id, available_models=["sonnet"], accepting=True
    )
    session.add(setting)
    await session.flush()
    account = LendingAccount(
        lending_id=setting.id, name="唯一的帳號", oauth_token_enc=b"x"
    )
    session.add(account)
    await session.flush()
    await session.refresh(setting, ["accounts"])
    return user, setting, account


async def test_follow_up_of_a_self_run_job_is_routed_to_yourself(session) -> None:
    user, setting, account = await _self_lender(session)
    parent = Job(
        borrower_id=user.id,
        source_type=SourceType.PASTE,
        prompt="第一輪",
        model="sonnet",
        status=JobStatus.SUCCEEDED,
        requested_lending_id=setting.id,  # 第一輪是「指定自己」
        lending_id=setting.id,
        account_id=account.id,
        transcript_key="jobs/x/transcript.jsonl",
    )
    session.add(parent)
    await session.flush()

    child = await follow_up(
        parent.id,
        FollowUp(prompt="第二輪", attachment_keys=[]),
        user=user,
        session=session,
    )

    # 使用者的症狀：新 job 掛在「自動」上，而自動永遠不會派給他 → 排隊到作廢。
    assert child.parent_job_id == parent.id
    row = await session.get(Job, child.id)
    assert row.requested_lending_id == setting.id, (
        "接著問沒有沿用「指定自己」，掉回「自動」—— 站台上只有他自己時這條是死路"
    )
    # 同一條規則的另一面：提交時的死路檢查要能過（第一輪就是靠這條過的）。
    await _check_model("sonnet", row.requested_lending_id, user.id, session)
