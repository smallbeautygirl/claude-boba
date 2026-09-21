"""產出檔案的挑選規則。

這是整個 repo 裡最不能寫錯的一段：`.home/` 裡掛著出租者的
`.credentials.json`，掃進上傳清單就等於把 Anthropic 憑證送上 S3。
"""

from __future__ import annotations

from pathlib import Path

from worker import _collect_artifacts


def _scaffold(root: Path) -> None:
    """重建一個 job 工作目錄的樣子。"""
    (root / ".home" / ".claude").mkdir(parents=True)
    (root / ".home" / ".claude" / ".credentials.json").write_text('{"secret":1}')
    (root / ".claude" / "skills" / "grilling").mkdir(parents=True)
    (root / ".claude" / "skills" / "grilling" / "SKILL.md").write_text("x")
    (root / "resume.jsonl").write_text("{}")


def test_picks_up_real_output(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "plan.md").write_text("計畫")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "deck.pptx").write_bytes(b"PK")

    names = {str(p.relative_to(tmp_path)) for p in _collect_artifacts(tmp_path)}
    assert names == {"plan.md", "out/deck.pptx"}


def test_never_uploads_credentials(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    picked = [str(p) for p in _collect_artifacts(tmp_path)]
    assert not any("credentials" in p for p in picked)
    assert not any(".home" in p for p in picked)


def test_skips_our_own_scaffolding(tmp_path: Path) -> None:
    """`.claude/` 是我們複製進去的 curated skills，不是 job 的產出。"""
    _scaffold(tmp_path)
    assert not any(".claude" in str(p) for p in _collect_artifacts(tmp_path))
    assert not any(p.name == "resume.jsonl" for p in _collect_artifacts(tmp_path))


def test_symlink_to_credentials_is_refused(tmp_path: Path) -> None:
    """最惡意的情況：job 做一個看起來無害的連結指向憑證。

    光看副檔名是看不出來的，所以規則是「一律跳過 symlink」。
    """
    _scaffold(tmp_path)
    (tmp_path / "report.md").symlink_to(
        tmp_path / ".home" / ".claude" / ".credentials.json"
    )

    picked = _collect_artifacts(tmp_path)
    assert picked == []


def test_symlink_escaping_the_workdir_is_refused(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("不該被上傳")
    (tmp_path / "innocent.txt").symlink_to(outside)

    assert _collect_artifacts(tmp_path) == []


def test_finds_transcript_from_a_normal_run(tmp_path: Path) -> None:
    from worker import _find_transcript

    d = tmp_path / ".home" / ".claude" / "projects" / "-job"
    d.mkdir(parents=True)
    (d / "abc.jsonl").write_text("{}")

    found = _find_transcript(tmp_path)
    assert found is not None and found.name == "abc.jsonl"


def test_finds_transcript_from_a_resumed_run(tmp_path: Path) -> None:
    """resume 時續跑的 transcript 落在 $HOME 根層，`projects/` 是空的。

    只找 projects/ 的話，續問鏈第二層以後永遠上傳不到新的 transcript，
    於是每個後續 job 都接在鏈的最前面，中間的對話全部遺失。
    """
    from worker import _find_transcript

    home = tmp_path / ".home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / "resume.jsonl").write_text("{}")
    (home / "continued-session.jsonl").write_text("{}")

    found = _find_transcript(tmp_path)
    assert found is not None and found.name == "continued-session.jsonl"


def test_resume_file_is_never_uploaded_as_output(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".home" / "resume.jsonl").write_text("{}")
    (tmp_path / ".home" / "continued.jsonl").write_text("{}")
    (tmp_path / "real-output.md").write_text("x")

    names = {str(p.relative_to(tmp_path)) for p in _collect_artifacts(tmp_path)}
    assert names == {"real-output.md"}
