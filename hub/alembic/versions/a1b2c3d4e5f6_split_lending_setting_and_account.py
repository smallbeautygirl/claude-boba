"""split worker into lending_settings + lending_accounts

Revision ID: a1b2c3d4e5f6
Revises: 93a6b609f7e3
Create Date: 2026-09-22

SPEC §4.12 / ADR-0001：一位代跑者可以出借多個 Claude 帳號。

`workers` 這張表同時扛著兩件事 —— 一組**條件**（上限／model／外網／接不接單）
與一個**帳號**（token／額度／併發）。第二個帳號進來時這兩件事就分開了：
條件屬於人，帳號屬於帳號。所以這張表一分為二。

順帶把領單協定從「一個 token 對一位代跑者」改成「一個 token 對這台主機」
（`worker_hosts`）：舊協定下 Hub 沒有「挑帳號」這個動作，帳號是自己跑來搶單的，
而那讓 §4.12 的派單規則做不出來。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "93a6b609f7e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. 領單主機 ───────────────────────────────────────────────
    op.create_table(
        "worker_hosts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, server_default="host"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("claude_code_version", sa.String(32)),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
    )

    # ── 2. 出借設定（條件，每人一份） ─────────────────────────────
    op.create_table(
        "lending_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "allow_full_network", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "available_models",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "job_budget_usd", sa.Numeric(10, 4), nullable=False, server_default="5"
        ),
        sa.Column("accepting", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # 每位擁有者收斂成一份條件。同一個人有多列 worker 時取**最嚴格**的那一份：
    # 上限取最小、model 取交集、外網要全部都開才算開。
    #
    # 放寬需要他本人同意，收緊不需要 —— 這個方向錯了會在他不知情的狀況下
    # 把某個帳號的上限調高，而那是他的錢。
    op.execute(
        """
        INSERT INTO lending_settings (
            id, owner_user_id, allow_full_network, available_models,
            job_budget_usd, accepting
        )
        SELECT gen_random_uuid(),
               w.owner_user_id,
               bool_and(w.allow_full_network),
               COALESCE(
                   (SELECT jsonb_agg(m) FROM (
                        SELECT jsonb_array_elements_text(w2.available_models) AS m
                        FROM workers w2
                        WHERE w2.owner_user_id = w.owner_user_id
                        GROUP BY m
                        HAVING count(*) = count(DISTINCT w2.id)
                    ) t),
                   '["sonnet","haiku"]'::jsonb
               ),
               min(w.job_budget_usd),
               bool_or(w.accepting)
        FROM workers w
        GROUP BY w.owner_user_id
        """
    )

    # ── 3. workers → lending_accounts ────────────────────────────
    op.rename_table("workers", "lending_accounts")

    op.add_column(
        "lending_accounts", sa.Column("lending_id", postgresql.UUID(as_uuid=True))
    )
    op.execute(
        """
        UPDATE lending_accounts a
           SET lending_id = s.id
          FROM lending_settings s
         WHERE s.owner_user_id = a.owner_user_id
        """
    )
    op.alter_column("lending_accounts", "lending_id", nullable=False)
    op.create_foreign_key(
        "fk_lending_accounts_lending",
        "lending_accounts",
        "lending_settings",
        ["lending_id"],
        ["id"],
    )

    op.add_column("lending_accounts", sa.Column("approver_note", sa.String(200)))
    op.add_column(
        "lending_accounts",
        sa.Column("needs_reauth", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "lending_accounts",
        sa.Column(
            "rate_limit_windows",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "lending_accounts", sa.Column("quota_updated_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "lending_accounts", sa.Column("last_assigned_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "lending_accounts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # 兩個 float 欄位併回整包 unifiedWindows。沒有 resetsAt —— 舊資料沒存過，
    # 而編一個出來比沒有更糟（畫面會顯示一個假的倒數）。
    op.execute(
        """
        UPDATE lending_accounts
           SET rate_limit_windows = jsonb_strip_nulls(jsonb_build_object(
                   'five_hour',
                   CASE WHEN utilization_five_hour IS NULL THEN NULL
                        ELSE jsonb_build_object('utilization', utilization_five_hour)
                   END,
                   'seven_day',
                   CASE WHEN utilization_seven_day IS NULL THEN NULL
                        ELSE jsonb_build_object('utilization', utilization_seven_day)
                   END
               )),
               quota_updated_at = last_seen_at
         WHERE utilization_five_hour IS NOT NULL
            OR utilization_seven_day IS NOT NULL
        """
    )

    # 搬走的欄位（條件），以及只有舊拉取式協定才需要的欄位。
    for col in (
        "owner_user_id",
        "allow_full_network",
        "available_models",
        "job_budget_usd",
        "accepting",
        "utilization_five_hour",
        "utilization_seven_day",
        "token",
        "online",
    ):
        op.drop_column("lending_accounts", col)

    # ── 4. jobs 的外鍵 ───────────────────────────────────────────
    op.alter_column("jobs", "worker_id", new_column_name="account_id")
    op.alter_column(
        "jobs", "requested_worker_id", new_column_name="requested_lending_id"
    )
    op.add_column("jobs", sa.Column("lending_id", postgresql.UUID(as_uuid=True)))
    op.execute(
        "UPDATE jobs SET lending_id = a.lending_id "
        "FROM lending_accounts a WHERE a.id = jobs.account_id"
    )
    # requested_* 原本指向 worker（帳號），現在要指向出借設定（人）。
    op.execute(
        "UPDATE jobs SET requested_lending_id = a.lending_id "
        "FROM lending_accounts a WHERE a.id = jobs.requested_lending_id"
    )
    op.create_foreign_key(
        "fk_jobs_lending", "jobs", "lending_settings", ["lending_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_jobs_requested_lending",
        "jobs",
        "lending_settings",
        ["requested_lending_id"],
        ["id"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "不提供 downgrade：條件從多列收斂成一份是有損的，倒回去只能猜。"
    )
