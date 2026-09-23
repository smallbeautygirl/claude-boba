"""帳號被回過 credits_required 的 model 清單

2026-09-23。站台問不出一個帳號跑不跑得動 Fable（spike 見 models.py 的註解），
所以改成跑失敗一次就記在這裡，派單據此跳過那個帳號。

Revision ID: d3f81a2c9b64
Revises: b7c1d9e3f204
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d3f81a2c9b64"
down_revision = "b7c1d9e3f204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default 是必要的：既有的列不能是 NULL，否則 `m not in (a.x or [])`
    # 之外的每一處都要再防一次 None。
    op.add_column(
        "lending_accounts",
        sa.Column(
            "credits_required_models",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("lending_accounts", "credits_required_models")
