# 站台 curated skills

這裡的 `commands/*.md` 會在每個 job 開始前被複製到工作目錄的 `.claude/commands/`，
所以全站每個 job 都能用，而且版本一致、可以 code review。

**為什麼不是直接用出租者自己安裝的 skill**：job 容器的 HOME 是乾淨的
（`.claude/rules/security.md` 紅線 2），出租者的個人 plugin 不會、也不該被載入。
要讓大家共用某個 skill，就把它放進這裡、走 PR。

借用者自己上傳的指令也會落在同一個目錄（`.claude/commands/`），但**這裡的檔案
後複製、會覆蓋同名者** —— 避免有人用自己的版本蓋掉團隊審過的指令。

`claude -p` 支援 slash command，實測確認（2026-09-21）：工作目錄層級的
`.claude/commands/ping.md` 可以用 `/ping` 觸發。
