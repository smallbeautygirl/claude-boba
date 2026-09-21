# Code Style Rules

Enforced by `ruff` (pinned 0.16.1). Follow these even when the linter is not running.
（與 `lighthouse-saas-api` 的規則一致 —— 兩個 repo 之間切換時不用換腦袋。）

## Python

- Formatter: `ruff format`（double quotes，ruff 預設 88 字元行寬 —— `pyproject.toml`
  沒設 `line-length`，所以不要手動折到更寬的欄位）
- Linter: `ruff check`，規則 `E, F, I, UP, B`（見 `pyproject.toml`）
- `from __future__ import annotations` 放在每個檔案最上面
- 用內建泛型：`list[str]`、`dict[str, int]`、`X | None` —— 不要從 `typing` import
  `List`、`Dict`、`Optional`
- 字串插值一律 f-string —— 不用 `%` 或 `.format()`
- 檔案路徑一律 `pathlib.Path` —— 不要用字串串接
- 固定的字串集合用 `Enum` / `StrEnum` —— 不要散落裸字串當識別碼
  （本專案的 job status、debt tier、debt status 都屬此類）

Import order（ruff/isort 強制）：

```
from __future__ import annotations
# 1. stdlib
# 2. third-party
# 3. local
```

## File Naming

| Context | Convention | Example |
|---|---|---|
| Python modules | `snake_case.py` | `token_pricing.py` |
| Test files | `test_<module>.py` | `test_token_pricing.py` |
| Config files | `snake_case.py` | `config.py` |

## 金額計算

**金額一律用 `Decimal`，不要用 `float`。** 這個專案的產出是要給人看的帳單，
浮點誤差累積出來的 `US$3.0000000000000004` 會直接摧毀可信度。
