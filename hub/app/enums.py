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


class DebtStatus(StrEnum):
    """債務狀態。

    結清由債主按（SPEC.md §4.8）—— 現實中請客是出租者被請，他最清楚有沒有發生。
    NUDGED 是借用者宣稱「我請過了」，把催促的責任放在欠債的人身上。
    """

    OPEN = "open"
    NUDGED = "nudged"
    SETTLED = "settled"


class WishCategory(StrEnum):
    """許願板的四類（docs/web-spec.md §12）。

    分類的作用只有兩個：牆上的篩選，以及新願望廣播的文案 ——
    **不分流給不同的人**，因為讀的人只有一個。
    `WANT_COMMAND` 值得獨立，因為它是唯一接得到既有機制的一類（SPEC.md §4.13）。

    `BROKEN` 與 `ROUGH_EDGE` 的界線是模糊的，我們知道 —— 那是選四類的已知代價，
    補償是願望可以編輯（含分類）。
    """

    BROKEN = "broken"
    WANT_COMMAND = "want_command"
    ROUGH_EDGE = "rough_edge"
    OTHER = "other"


class WishTarget(StrEnum):
    """一個反應或一張貼圖掛在願望上，還是掛在留言上。

    名字不叫 `ReactionTarget`：`WishImage` 也用它當判別欄位，而**圖不是反應** ——
    那個名字在一半的呼叫點上是假的。
    """

    WISH = "wish"
    COMMENT = "comment"


class CredentialKind(StrEnum):
    """一個出借帳號的憑證是哪一種。security.md 紅線 2 的那張表。

    兩種會並存一段時間 —— 現有的出借帳號全是 `SETUP_TOKEN`，而這決定**派單前
    要不要先 refresh**：`SETUP_TOKEN` 一年期、拿了就用；`OAUTH` 八小時就死，
    每次派單前都要確認手上那張還活著。
    """

    SETUP_TOKEN = "setup_token"
    OAUTH = "oauth"
