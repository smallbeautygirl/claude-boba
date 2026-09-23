"""jobs.last_heartbeat_at：worker 最後一次回報的時間

2026-09-23。worker 在 job 中途炸掉、沒回報 result，hub 上那個 job 就永遠「執行中」——
hub 只知道它何時開始，不知道 worker 還在不在。worker 每 3 秒有一次心跳，記下來
之後 app/orphans.py 才有依據替安靜掉的 job 結案。

既有的執行中 job 這欄是 NULL，orphans 用 coalesce(last_heartbeat_at, started_at,
claimed_at) 判定，所以不需要回填。

Revision ID: e4a7c1b2d9f0
Revises: d3f81a2c9b64
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4a7c1b2d9f0"
down_revision = "d3f81a2c9b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "last_heartbeat_at")
