"""wish board

Revision ID: b7c1d9e3f204
Revises: a1b2c3d4e5f6
Create Date: 2026-09-22

許願板的四張表（docs/web-spec.md §12）。

**這是鷹架。** 許願板有寫死的下架條件 —— Phase 2 用真實帳號驗完，或連續 30 天
沒有新的一則，先到者為準。到那天 `downgrade()` 就是拆牆的一半（另一半是 MinIO
的 `wishes/` prefix 與前端那個相依），所以它要真的能跑，不是形式。

四張表刻意不跟 `jobs` 有任何外鍵：一則願望不指向任何 job，連 job id 都不存。
拆的時候不會扯到別的東西。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1d9e3f204"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# native_enum=False：值存成字串，改值不用再跑一次 migration（見 models._enum）。
_CATEGORY = sa.Enum(
    "broken",
    "want_command",
    "rough_edge",
    "other",
    name="wishcategory",
    native_enum=False,
    length=16,
)
_TARGET = sa.Enum("wish", "comment", name="wishtarget", native_enum=False, length=16)


def upgrade() -> None:
    op.create_table(
        "wishes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "author_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("category", _CATEGORY, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        # 三個 fulfilled_* 同生同滅：要嘛全 NULL，要嘛全有值。約束寫在 DB 裡，
        # 因為「實現了但沒有連結」正是這個功能要避免的那個狀態（空頭宣告）。
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fulfilled_by",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("fulfilled_link", sa.String(1024), nullable=True),
        sa.CheckConstraint(
            "(fulfilled_at IS NULL AND fulfilled_by IS NULL AND fulfilled_link IS NULL)"
            " OR (fulfilled_at IS NOT NULL AND fulfilled_by IS NOT NULL"
            " AND fulfilled_link IS NOT NULL)",
            name="ck_wish_fulfilled_all_or_nothing",
        ),
    )
    op.create_index("ix_wishes_author_id", "wishes", ["author_id"])

    op.create_table(
        "wish_comments",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "wish_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("wishes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_wish_comments_wish_id", "wish_comments", ["wish_id"])

    op.create_table(
        "wish_reactions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_type", _TARGET, nullable=False),
        # 沒有外鍵：它指向兩張表之一。孤兒列由 router 清（_purge）。
        sa.Column("target_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id", sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("emoji", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # 「同一顆再按一次是取消」在應用層做，但唯一鍵是它的安全網 ——
        # 兩個同時到達的請求不該讓同一個人在同一顆上留下兩列。
        sa.UniqueConstraint(
            "target_type", "target_id", "user_id", "emoji", name="uq_wish_reaction"
        ),
    )
    op.create_index(
        "ix_wish_reactions_target", "wish_reactions", ["target_type", "target_id"]
    )

    op.create_table(
        "wish_images",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("target_type", _TARGET, nullable=False),
        sa.Column("target_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(768), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_wish_images_target", "wish_images", ["target_type", "target_id"]
    )


def downgrade() -> None:
    """拆牆的一半。

    ⚠️ 這裡刪掉的是**紀錄**，MinIO `wishes/` prefix 底下的圖還在。
    真的要拆乾淨，那個 prefix 要另外刪 —— 它刻意不在 30 天 lifecycle 裡
    （SPEC.md §8），所以不會自己消失。
    """
    op.drop_table("wish_images")
    op.drop_table("wish_reactions")
    op.drop_table("wish_comments")
    op.drop_table("wishes")
