"""帶 codebase 進來的 job 要跑比較久。

timeout 在這個系統裡**不是成本閘門** —— `--max-budget-usd` 才是，而它封在代跑者
自己填的上限。timeout 擋的是「不花 token 的卡死」（卡在 git、卡在無限迴圈的
Bash），那種 job 撞不到 budget，會一直佔著 `max_concurrency` 兩個執行位其中一個。

所以分流的條件是 zip：它是唯一一個「慢」的原因直接寫在條件裡的 —— zip 意味著
要解開、要摸結構、要跑東西。有附件就給長的太鬆（一張截圖也會拿到 30 分鐘）。
"""

from __future__ import annotations

from worker import LONG_TIMEOUT_SECONDS, settings, timeout_for_inputs


def test_plain_job_keeps_the_short_timeout() -> None:
    assert timeout_for_inputs({}) == settings.timeout_seconds


def test_attachments_alone_do_not_buy_more_time() -> None:
    inputs = {"螢幕截圖.png": "d1", "需求.docx": "d2"}
    assert timeout_for_inputs(inputs) == settings.timeout_seconds


def test_zip_gets_the_long_timeout() -> None:
    assert timeout_for_inputs({"repo.zip": "d1"}) == LONG_TIMEOUT_SECONDS


def test_zip_among_other_files_still_counts() -> None:
    inputs = {"說明.md": "d1", "repo.zip": "d2"}
    assert timeout_for_inputs(inputs) == LONG_TIMEOUT_SECONDS


def test_extension_matching_ignores_case() -> None:
    assert timeout_for_inputs({"REPO.ZIP": "d1"}) == LONG_TIMEOUT_SECONDS


def test_renamed_duplicate_still_counts() -> None:
    """`_place_attachments()` 對同名檔加序號 —— `repo-2.zip` 仍然是 zip。"""
    assert timeout_for_inputs({"repo-2.zip": "d1"}) == LONG_TIMEOUT_SECONDS


def test_a_lender_who_set_a_longer_timeout_keeps_it() -> None:
    """長的那個是**下限不是上限**。

    主機管理者把 TIMEOUT_SECONDS 調到一小時，不該因為附件裡有 zip 就被砍回 30 分鐘。
    """
    original = settings.timeout_seconds
    settings.timeout_seconds = LONG_TIMEOUT_SECONDS * 2
    try:
        assert timeout_for_inputs({"repo.zip": "d1"}) == LONG_TIMEOUT_SECONDS * 2
    finally:
        settings.timeout_seconds = original
