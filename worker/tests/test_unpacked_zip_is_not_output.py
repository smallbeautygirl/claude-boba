"""解開的 repo 不是 job 的產出。

委託者帶 codebase 進來的路徑是「壓成一個 zip，Claude 在容器裡自己解」。解開之後
那棵樹上的每個檔案對 `_collect_artifacts()` 來說都是全新的 —— 沒有這條規則，
「產出的檔案」會被 50 個 `.git/objects/…` 塞滿，而 Claude 真正寫出來的東西被
`MAX_ARTIFACTS` 擠掉。使用者拿回一堆自己傳進去的東西，拿不到他要的那份。

比對用 zip 自己的 **CRC32 與大小**，不是路徑：`unzip` 可能解在根層、也可能解進
一個子目錄，而路徑對不上就整條規則失效。內容一樣就是沒動過，解在哪裡都一樣。

**改過的要傳回去** —— 這與既有附件的規則是同一條（`_collect_artifacts` 的
`inputs` 雜湊）：「幫我改這份 code」的成果就是那些檔案。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from worker import _collect_artifacts, unpacked_fingerprints


def _scaffold(root: Path) -> None:
    (root / ".home" / ".claude").mkdir(parents=True)
    (root / ".home" / ".claude" / ".credentials.json").write_text('{"secret":1}')


def _make_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)


def test_files_that_came_out_of_the_zip_are_not_output(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _make_zip(tmp_path / "repo.zip", {"src/main.py": "print(1)\n", "README.md": "# hi"})
    # Claude 解在根層
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)\n")
    (tmp_path / "README.md").write_text("# hi")
    (tmp_path / "報告.md").write_text("我找到三個 bug")

    seen = unpacked_fingerprints(tmp_path)
    names = {
        str(p.relative_to(tmp_path))
        for p in _collect_artifacts(tmp_path, unpacked=seen)
    }
    assert names == {"報告.md", "repo.zip"}


def test_it_does_not_matter_where_the_zip_was_unpacked(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _make_zip(tmp_path / "repo.zip", {"src/main.py": "print(1)\n"})
    # `unzip -d repo/` —— 路徑對不上，內容一樣
    (tmp_path / "repo" / "src").mkdir(parents=True)
    (tmp_path / "repo" / "src" / "main.py").write_text("print(1)\n")

    seen = unpacked_fingerprints(tmp_path)
    names = {
        str(p.relative_to(tmp_path))
        for p in _collect_artifacts(tmp_path, unpacked=seen)
    }
    assert names == {"repo.zip"}


def test_a_file_claude_changed_still_comes_back(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _make_zip(tmp_path / "repo.zip", {"src/main.py": "print(1)\n"})
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(2)  # 修好了\n")

    seen = unpacked_fingerprints(tmp_path)
    names = {
        str(p.relative_to(tmp_path))
        for p in _collect_artifacts(tmp_path, unpacked=seen)
    }
    assert names == {"src/main.py", "repo.zip"}


def test_git_is_never_output(tmp_path: Path) -> None:
    """`.git/` 不必比對就該排除。

    Claude 跑了 `git add` 或 `git commit` 之後，objects 目錄裡會多出 zip 裡沒有的
    檔案 —— 那些在 CRC 比對下是「新的」，但沒有人想一個一個下載 git 的內部檔案。
    """
    _scaffold(tmp_path)
    (tmp_path / ".git" / "objects" / "ab").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "ab" / "cdef").write_bytes(b"\x01\x02")
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "報告.md").write_text("x")

    names = {str(p.relative_to(tmp_path)) for p in _collect_artifacts(tmp_path)}
    assert names == {"報告.md"}


def test_a_job_with_no_zip_reads_nothing(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "簡報.pptx").write_bytes(b"PK")
    assert unpacked_fingerprints(tmp_path) == set()


def test_a_broken_zip_does_not_take_the_job_down(tmp_path: Path) -> None:
    """壞掉的 zip 只代表「沒有東西可以排除」，不該讓整個上傳流程爆掉。"""
    _scaffold(tmp_path)
    (tmp_path / "repo.zip").write_bytes(b"not actually a zip")
    assert unpacked_fingerprints(tmp_path) == set()
