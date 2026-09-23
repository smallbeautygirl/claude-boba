"""拆掉 jobs.requested_lending_id 上那條指錯表的舊 FK

Revision ID: d9b2e4f6a8c1
Revises: c3e8b5a71f42
Create Date: 2026-09-23

## 為什麼

a1b2c3d4e5f6 把 `workers` 拆成 `lending_settings` + `lending_accounts` 時，
把 `jobs.requested_worker_id` **改名**成 `requested_lending_id`、再加一條指
`lending_settings` 的 FK（`fk_jobs_requested_lending`）—— 但舊的
`jobs_requested_worker_id_fkey` 沒有拆。改名不會動 FK，於是它跟著被改名的
`workers` 表一路指到 `lending_accounts`。

同一個欄位掛兩條 FK 指兩張表，值不可能同時滿足：**指定代跑者的 job 一律 500**。

dev 從來沒踩到，是因為那支 migration 把舊 worker 的 id 同時複製成兩張表的 id
（每個 lending_settings.id 都剛好也是某個 lending_accounts.id），而 dev 的 job
一筆都沒指定過代跑者。正式站是乾淨的庫，第一筆就炸（2026-09-23）。

`jobs_worker_id_fkey`（account_id → lending_accounts）指的是對的表，只是名字還
叫 worker；順手改名成 `fk_jobs_account`，跟另外兩條同一套命名。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision = "d9b2e4f6a8c1"
down_revision: str | Sequence[str] | None = "c3e8b5a71f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("jobs_requested_worker_id_fkey", "jobs", type_="foreignkey")
    op.drop_constraint("jobs_worker_id_fkey", "jobs", type_="foreignkey")
    op.create_foreign_key(
        "fk_jobs_account", "jobs", "lending_accounts", ["account_id"], ["id"]
    )


def downgrade() -> None:
    # 舊的那條是 bug，downgrade 不把它加回來 —— 加回來就是把 500 加回來。
    op.drop_constraint("fk_jobs_account", "jobs", type_="foreignkey")
    op.create_foreign_key(
        "jobs_worker_id_fkey", "jobs", "lending_accounts", ["account_id"], ["id"]
    )
