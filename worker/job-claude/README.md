# Job 容器的 `.claude/`

這整個目錄會在每個 job 開始前複製到工作目錄的 `.claude/`，所以每個 job 都能用，
而且版本一致、可以 code review。

```
commands/   slash command（單一 .md）
skills/     SKILL.md 格式的 skill
```

兩者都實測確認過會在專案層級被載入（2026-09-21，CLI 2.1.278）。

## 為什麼不是用出租者自己安裝的 plugin

job 容器的 HOME 是乾淨的（`.claude/rules/security.md` 紅線 2），出租者的個人
plugin 不會、也不該被載入。要讓大家共用某個 skill，就放進這裡、走 PR ——
這樣它是被審過的、全站一致的，而且不會因為某個出租者升級了外掛就換行為。

借用者自己上傳的指令也會落在同一個目錄，但**這裡的檔案後複製、會覆蓋同名者**，
避免有人用自己的版本蓋掉團隊審過的指令。

## `skills/` 的來源與授權

來自 [mattpocock/skills](https://github.com/mattpocock/skills) **v1.2.3**，MIT 授權，
著作權聲明保留在 `skills/LICENSE.mattpocock`。

**只挑了在這個環境真的跑得動的 8 個。** 上游有 37 個，其餘大多需要一個 codebase
（`tdd`、`code-review`、`diagnosing-bugs`、`resolving-merge-conflicts`…）或外部工具
（`research` 要 WebFetch，被 egress 白名單擋；`to-tickets`、`triage` 要 issue tracker）。
在借用者還不能上傳檔案之前，那些列出來只會讓人點了失望。

**版本釘死在 1.2.3，不追 main。** 理由同 Dockerfile 釘版本：上游更新會無聲改變
每個 job 的行為，而這裡的內容是會指導 Claude 用 Bash 的 —— 要進來就要被看過。
升級 = 一個 PR。
