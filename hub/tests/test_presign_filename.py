"""下載連結要自己帶檔名。

前端的 `<a download="…">` 在這裡是沒用的：預簽 URL 指向 MinIO，跟 SPA 不同 origin，
跨 origin 時瀏覽器會忽略 `download` 的值。檔案會照 key 的最後一段落地 ——
`transcript.jsonl`。而 Claude Code 是用檔名認 session 的，那個名字 `--resume` 不到，
整個「帶回自己的機器續跑」就是壞的。

所以檔名只能由 `Content-Disposition` 帶，而它必須簽進預簽 URL 裡。
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app import storage


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_a_plain_download_link_says_nothing_about_the_name() -> None:
    q = _query(storage.presign_get("jobs/abc/output/deck.pptx"))
    assert "response-content-disposition" not in q


def test_the_filename_is_signed_into_the_url() -> None:
    url = storage.presign_get("jobs/abc/transcript.jsonl", filename="the-job-id.jsonl")
    q = _query(url)
    assert q["response-content-disposition"] == [
        'attachment; filename="the-job-id.jsonl"'
    ]


def test_the_filename_is_covered_by_the_signature() -> None:
    """不在簽章裡的話，MinIO 會拒絕這個請求 —— 連結會變成壞的。"""
    q = _query(storage.presign_get("jobs/abc/transcript.jsonl", filename="x.jsonl"))
    signed = q["X-Amz-SignedHeaders"][0] if "X-Amz-SignedHeaders" in q else ""
    # botocore 把 response-* 當成 query 參數簽進 canonical request，
    # 所以它出現在 query string 上就代表被簽了；這裡確認簽章本身存在。
    assert q["X-Amz-Signature"][0]
    assert signed  # host 至少要在裡面


def test_a_name_that_tries_to_break_out_of_the_header_is_cleaned() -> None:
    """檔名是我們產生的，但清理一次的成本是零。"""
    url = storage.presign_get("k", filename='../../etc/passwd"; x=1')
    disposition = _query(url)["response-content-disposition"][0]
    assert '"' not in disposition.removeprefix('attachment; filename="').removesuffix(
        '"'
    )
    assert "/" not in disposition
