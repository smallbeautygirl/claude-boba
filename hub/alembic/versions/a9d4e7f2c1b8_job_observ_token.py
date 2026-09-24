"""jobs.observ_token_enc：「查 Observ 事件」的 job 排隊期間暫存的委託者 Observ token

2026-09-24。第一個要連外部服務的指令。ADR-0002 原本要 hub 另收一份服務憑證（帳密）
加密保管；實作時發現登入 boba 用的那張 Observ token 就夠（72 小時、job 最多活 25 分鐘），
所以改成：瀏覽器在送出「查 Observ 事件」時把登入 token 一起帶上，hub 驗過是本人、
剩夠久之後**加密存在 job 上**，派給 worker 的那一刻清成 NULL。

只有排隊那段有值。既有 job 全部 NULL，不需要回填。

Revision ID: a9d4e7f2c1b8
Revises: e5c7a9b1d3f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9d4e7f2c1b8"
down_revision = "e5c7a9b1d3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("observ_token_enc", sa.LargeBinary(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "observ_token_enc")
