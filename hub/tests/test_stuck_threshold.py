"""「卡住了」的門檻要看這個 job 拿到的是哪一個逾時。

worker 給帶 zip 的 job 30 分鐘、其餘 10 分鐘（`worker.timeout_for_inputs`）。
管理頁若一律用 10 分鐘那條線，每個健康的 code job 都會從第 12 分鐘一路列在
「卡住的 job」裡到第 30 分鐘 —— 那個面板的用途是「一眼看出哪個永遠不會結束」，
被例行事件塞滿之後它就不再被人看了。

反過來一律用 30 分鐘也不行：一個普通 job 在第 13 分鐘還在跑，那是**確定壞了**
（worker 的逾時是 600 秒，它早該被殺掉），那個訊號不該被延後 20 分鐘。
"""

from __future__ import annotations

from app.routers.admin import (
    RUNNING_STUCK_SECONDS,
    ZIP_RUNNING_STUCK_SECONDS,
    running_stuck_seconds,
)


def test_a_plain_job_keeps_the_short_line() -> None:
    assert running_stuck_seconds([]) == RUNNING_STUCK_SECONDS
    assert running_stuck_seconds(["attachments/u/1/需求.docx"]) == RUNNING_STUCK_SECONDS


def test_a_job_carrying_a_zip_gets_the_long_line() -> None:
    keys = ["attachments/u/1/repo.zip"]
    assert running_stuck_seconds(keys) == ZIP_RUNNING_STUCK_SECONDS


def test_case_does_not_matter() -> None:
    assert running_stuck_seconds(["attachments/u/1/REPO.ZIP"]) == (
        ZIP_RUNNING_STUCK_SECONDS
    )


def test_a_zip_among_other_attachments_still_counts() -> None:
    keys = ["attachments/u/1/說明.md", "attachments/u/2/repo.zip"]
    assert running_stuck_seconds(keys) == ZIP_RUNNING_STUCK_SECONDS


def test_the_long_line_is_past_the_workers_long_timeout() -> None:
    """門檻要在 worker 真的會殺掉它之後，否則列出來的是健康的 job。"""
    assert ZIP_RUNNING_STUCK_SECONDS > 1800
    assert RUNNING_STUCK_SECONDS > 600
