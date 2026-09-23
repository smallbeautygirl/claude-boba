"""account claude identity

Revision ID: f1a9c47b20de
Revises: e4a7c1b2d9f0
Create Date: 2026-09-23

出借帳號實際上是哪一個 Claude 帳號（app/claude_profile.py）。

在這之前站台從頭到尾沒看過帳號的身分 —— 同一個 Claude 帳號授權兩次會變成兩個
看起來不相干的出借帳號，而它們共用同一份額度與 rate limit。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "f1a9c47b20de"
down_revision: str | Sequence[str] | None = "e4a7c1b2d9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lending_accounts",
        sa.Column("claude_account_uuid", sa.String(64), nullable=True),
    )
    op.add_column(
        "lending_accounts", sa.Column("claude_email", sa.String(200), nullable=True)
    )
    op.add_column(
        "lending_accounts", sa.Column("claude_plan", sa.String(20), nullable=True)
    )
    op.add_column(
        "lending_accounts",
        sa.Column(
            "claude_identity_checked_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_index(
        "ix_lending_accounts_claude_account_uuid",
        "lending_accounts",
        ["claude_account_uuid"],
    )
    # 同一位代跑者不能把同一個 Claude 帳號出借兩次。
    #
    # **Postgres 的唯一索引不擋多個 NULL**，而那正是這裡需要的行為：反查不到身分
    # 的帳號（scope 不夠、反查關著、Anthropic 掛了）全部是 NULL，它們不該互相衝突。
    # 所以這條約束是「認得出來的就去重，認不出來的放行」—— 而不是「先擋再說」。
    #
    # 範圍是**每位代跑者**，不是全站。兩個人把同一個 Claude 帳號都借出來也是錯的
    # （額度會被重複計算），但那是另一個問題，而且沒有人遇到過 —— 見 SPEC §4.12。
    op.create_unique_constraint(
        "uq_lending_account_claude_uuid",
        "lending_accounts",
        ["lending_id", "claude_account_uuid"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_lending_account_claude_uuid", "lending_accounts", type_="unique"
    )
    op.drop_index(
        "ix_lending_accounts_claude_account_uuid", table_name="lending_accounts"
    )
    for col in (
        "claude_identity_checked_at",
        "claude_plan",
        "claude_email",
        "claude_account_uuid",
    ):
        op.drop_column("lending_accounts", col)
