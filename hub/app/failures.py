"""把 job 的失敗分類成使用者看得懂、而且知道下一步的東西。

`docs/web-spec.md` §5 定了三類，揭露程度不同：

| 類型 | 給使用者看什麼 |
|---|---|
| 可修復 | 完整原因 + 明確下一步 |
| 系統問題 | 「系統問題，已記錄」+ job id |
| Claude 本身 | 原始訊息（那是 Claude 給使用者的，不是我們的） |

分類做在 Hub 而不是前端：同一份判斷未來 Teams 通知也要用，而且前端正在改版，
把規則放在會被重寫的地方等於會遺失。

不論哪一類都不計債（SPEC.md §5）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from .enums import JobStatus


class FailureKind(StrEnum):
    FIXABLE = "fixable"
    SYSTEM = "system"
    CLAUDE = "claude"


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    title: str
    hint: str | None = None
    # 顯示原始訊息嗎。系統問題不顯示 —— 使用者看不懂，也幫不上忙。
    show_detail: bool = True
    # 這個 job 是不是被 egress 白名單擋掉的。前端據此顯示〔改送給開放網路的
    # worker〕—— web-spec §5：「那個按鈕是重點，不是那句話。」
    blocked_by_network: bool = False

    def as_dict(self) -> dict:
        return asdict(self) | {"kind": str(self.kind)}


# 被白名單擋住時的跡象。job 內容一律不進判斷 —— 只看錯誤訊息。
_NETWORK_SIGNS = (
    "403",
    "econnrefused",
    "enotfound",
    "getaddrinfo",
    "proxy",
    "tunneling socket",
    "registry.npmjs.org",
    "pypi.org",
)
_PACKAGE_SIGNS = ("npm install", "pip install", "npm error", "yarn add", "uv pip")

_BY_STATUS: dict[JobStatus, Failure] = {
    JobStatus.TIMEOUT: Failure(
        FailureKind.FIXABLE,
        "超過時間上限，已中止",
        "把要做的事拆小一點再送一次。這次不計債。",
    ),
    JobStatus.OVER_BUDGET: Failure(
        FailureKind.FIXABLE,
        "超過花費上限，已中止",
        "換成 Haiku，或把範圍縮小再送一次。這次不計債。",
    ),
    JobStatus.CANCELLED: Failure(
        FailureKind.FIXABLE,
        "出租者中止了這個 job",
        None,
        show_detail=False,
    ),
    JobStatus.EXPIRED: Failure(
        FailureKind.SYSTEM,
        "目前沒人有空，這個 job 已作廢",
        "等一下再送，或直接指定一位線上的出租者。",
        show_detail=False,
    ),
}

_SYSTEM_KINDS = {"no_result_event", "worker_error", "storage_error"}

# 代跑者的授權失效。這一類**不是委託者能修的**，所以它跟 FIXABLE 不同 ——
# 給他一個「再試一次」的建議只會讓他再燒一輪等待。
_AUTH_FAILURE = Failure(
    FailureKind.SYSTEM,
    "代跑者的授權失效了，這個 job 沒跑成",
    "不是你的問題，也不計債。那個帳號已經停止接單，重送會換一位代跑者；"
    "如果站台上只有他一個人，等他重新授權之後再送。",
    show_detail=False,
)


# 代跑者的帳號要買 usage credits 才跑得動這個 model（2026-09-23）。
#
# **不沿用「原樣顯示 Claude 的訊息」那一條。** Claude 那句話是
# 「manage usage credits at claude.ai/settings/usage」—— 它假設看訊息的人就是
# 帳號持有人，但這裡看畫面的是委託者，要買 credits 的是代跑者。原樣顯示會把
# 委託者導到他自己的帳單頁，去買一個對這個 job 完全沒有用的東西。
#
# 這跟 CLAUDE.md 記的「超出預算，換 Haiku 再試」是同一類的錯：訊息本身沒說謊，
# 但它指向的下一步是錯的，而那比只說「失敗了」更糟。
def _credits_required(lender: str | None, model: str | None) -> Failure:
    who = lender or "代跑者"
    what = f"{model} " if model else ""
    return Failure(
        FailureKind.SYSTEM,
        f"{who} 的帳號要買 usage credits 才跑得動 {what}job",
        f"不是你的問題，也不計債，這個 job 也沒有花到錢。{who} 的帳號已經不會再"
        f"接這個 model 的 job —— 換個人、換成 Sonnet，或等他買了 credits 再送。",
        show_detail=False,
    )


def classify(
    status: JobStatus,
    error_kind: str | None,
    error_detail: str | None,
    *,
    lender: str | None = None,
    model: str | None = None,
) -> Failure | None:
    """成功的 job 回 None。"""
    if not status.is_terminal or status is JobStatus.SUCCEEDED:
        return None

    blob = f"{error_kind or ''} {error_detail or ''}".lower()

    # ⚠️ **這一條要排在 _BY_STATUS 前面。** 狀態本身可能是錯的 —— worker 曾經
    # 把每一個失敗都標成 over_budget（對整包 result JSON 比對 "budget"，而
    # result 裡本來就有那個欄位）。那個 bug 修掉了，但「先看原因、再看狀態」
    # 這個順序本身是對的：認證失效跟花費上限是兩件完全不同的事，而給錯建議
    # （「換成 Haiku 再送一次」）比只說「失敗了」更糟。
    if error_kind == "auth_failed" or "oauth access token is invalid" in blob:
        return _AUTH_FAILURE

    # 排在 _BY_STATUS 前面的理由同上一條：原因比狀態可信。
    if error_kind == "credits_required":
        return _credits_required(lender, model)

    if fixed := _BY_STATUS.get(status):
        return fixed

    # 白名單擋到是 RD 最常撞的牆。只寫「執行失敗」的話，他們會以為工具壞了。
    if any(s in blob for s in _NETWORK_SIGNS) and any(
        p in blob for p in _PACKAGE_SIGNS
    ):
        return Failure(
            FailureKind.FIXABLE,
            "這個 job 需要連外網安裝套件，但該 worker 是白名單網路模式",
            "改送給開放網路的 worker，或改用不需要安裝套件的做法。",
            blocked_by_network=True,
        )

    if (error_kind or "") in _SYSTEM_KINDS:
        return Failure(
            FailureKind.SYSTEM,
            "系統問題，已記錄",
            "不是你的錯，也不計債。把這個 job 的網址貼給維護者就好。",
            show_detail=False,
        )

    # 其餘當成 Claude 自己回的 —— 那段訊息是 Claude 給使用者的，不是我們的，
    # 原樣顯示比我們改寫有用。
    return Failure(FailureKind.CLAUDE, "Claude 沒有完成這個 job", "這次不計債。")
