"""oauth credentials

Revision ID: c3e8b5a71f42
Revises: f1a9c47b20de
Create Date: 2026-09-23

站台自己跑 OAuth 拿到的憑證（SPEC §11 #13、security.md 紅線 2 第三列）。

跟 `setup_token` 那種並存：現有的出借帳號全部標成 `setup_token`，行為完全不變。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3e8b5a71f42"
down_revision: str | Sequence[str] | None = "f1a9c47b20de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND = sa.Enum(
    "setup_token", "oauth", name="credentialkind", native_enum=False, length=16
)


def upgrade() -> None:
    # 既有的列全部是 setup_token。**先帶 server_default 再拿掉** ——
    # 不帶的話 nullable=False 在有資料的表上會直接失敗（cef593906d68 的教訓）。
    op.add_column(
        "lending_accounts",
        sa.Column(
            "credential_kind", _KIND, nullable=False, server_default="setup_token"
        ),
    )
    op.alter_column("lending_accounts", "credential_kind", server_default=None)

    op.add_column(
        "lending_accounts",
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "lending_accounts",
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "lending_accounts",
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "lending_accounts",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("lending_accounts", "scopes", server_default=None)


def downgrade() -> None:
    for col in (
        "scopes",
        "refresh_expires_at",
        "access_expires_at",
        "refresh_token_enc",
        "credential_kind",
    ):
        op.drop_column("lending_accounts", col)
