"""出借設定的外網預設改成開

Revision ID: e5c7a9b1d3f2
Revises: d9b2e4f6a8c1
Create Date: 2026-09-23

## 為什麼

紅線 3「egress 白名單」寫的時候，job 跑在代跑者自己的電腦上，擋外網保護的是
那台電腦。2026-09-22 之後 job 在共用主機的拋棄式容器裡跑，那個理由不存在了；
而白名單的代價是每個貼連結進來的人都撞一次「打不開」（2026-09-23 正式站第一個
job 就是）。剩下的真實風險只有容器裡那張 8 小時的 access token 可能被惡意內容
送出去 —— 有上限、能撤銷，代跑者不接受可以自己關。決定：預設開，可關。

## 既有的列也一起翻成開

這一刻站台上只有一位代跑者，沒有人是刻意把它關著的 —— 那個開關以前是「預設關、
要開的人自己開」，關著只代表沒動過。翻過去等於讓既有的人跟新人拿到同一個預設。
之後若有人刻意關，這支 migration 已經跑過，不會再碰。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "e5c7a9b1d3f2"
down_revision: str | Sequence[str] | None = "d9b2e4f6a8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "lending_settings", "allow_full_network", server_default=sa.text("true")
    )
    op.execute("UPDATE lending_settings SET allow_full_network = true")


def downgrade() -> None:
    op.alter_column(
        "lending_settings", "allow_full_network", server_default=sa.text("false")
    )
    # 不把值翻回去：那會把有人刻意開著的也關掉。
