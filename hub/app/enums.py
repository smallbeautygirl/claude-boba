"""固定字串集合。依 .claude/rules/code-style.md，不使用裸字串當識別碼。"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """SPEC.md §5 的狀態機。只有 SUCCEEDED 會產生人情債。"""

    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    OVER_BUDGET = "over_budget"

    @property
    def is_terminal(self) -> bool:
        return self not in (JobStatus.QUEUED, JobStatus.CLAIMED, JobStatus.RUNNING)

    @property
    def creates_debt(self) -> bool:
        """SPEC.md §5：失敗、逾時、取消、超出預算一律不計債。

        嚴格說取消時 token 確實燒掉了，但一條簡單的規則比一條公平的規則值錢 ——
        不值得為此處理「跑到一半取消該付多少」的爭議。
        """
        return self is JobStatus.SUCCEEDED


class SourceType(StrEnum):
    """借用者的上下文從哪裡來。SPEC.md §4.2。"""

    PASTE = "paste"
    TRANSCRIPT = "transcript"


class DebtTier(StrEnum):
    """SPEC.md §4.7 的級距。"""

    NONE = "none"
    DRINK = "drink"
    COFFEE = "coffee"
    BENTO = "bento"
    FEAST = "feast"
