# Git Commit Rules

All commits follow the **Conventional Commits** specification.
（沿用 `lighthouse-saas-api` 的慣例，只換掉 scope 清單。）

## Format

```
<type>(<scope>): <emoji> <description>

[optional body — explain WHY]

[optional footers]
```

## Types

| Type | Emoji | Use for |
|---|---|---|
| `feat` | ✨ | New feature or capability |
| `fix` | 🐛 | Bug fix |
| `refactor` | ♻️ | Code restructure without behaviour change |
| `perf` | ⚡️ | Performance improvement |
| `test` | ✅ | Adding or fixing tests |
| `docs` | 📝 | Documentation only |
| `style` | 🎨 | Formatting, linting (no logic change) |
| `chore` | 🔧 | Config, dependencies, tooling |
| `ci` | 👷 | CI/CD pipeline changes |
| `build` | 📦 | Build system or packaging |
| `revert` | ⏪️ | Reverting a previous commit |

## Scopes

| Scope | Area |
|---|---|
| `hub` | 中央服務：API、派單、Web UI |
| `worker` | 出租者端的 worker |
| `web` | 前端頁面 |
| `auth` | Observ 認證 |
| `billing` | Token 計量、金額折算、人情債 |
| `storage` | MinIO / presigned URL |
| `notify` | Teams webhook 與站內通知 |
| `db` | Schema 與 migration |
| `deps` | 相依套件 |
| `ci` | CI 設定 |

## Rules

- Description: imperative mood, max 72 chars, **no period** at the end
- Body: separated by one blank line, explains motivation (not "what")
- Breaking changes: add `!` before colon AND a `BREAKING CHANGE:` footer
- Omit scope if the change genuinely spans multiple areas

## Examples

```
feat(billing): ✨ add cache-aware token cost calculation

fix(worker): 🐛 kill container when job exceeds timeout

docs(spec): 📝 record egress allowlist tradeoff for RD workloads

chore(deps): 🔧 pin ruff to 0.16.1
```

## What NOT to Do

- No `fix: fix bug` — be specific
- No `feat: update code` — name the feature
- No present tense: `adds` → `add`
- No committing `.env`, `__pycache__/`
- Never `git commit --no-verify` to skip hooks
