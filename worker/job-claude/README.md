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

> ⚠️ **2026-09-22 訂正：上面那句的前提已經不成立，現在是 11 個。**
> 附件上傳做完了（`hub/app/routers/uploads.py`），RD 的路徑是「把專案壓成一個含
> `.git` 的 zip 傳上來，Claude 在容器裡自己解」。「需要一個 codebase」因此不再是
> 排除理由，`tdd`、`diagnosing-bugs`、`resolving-merge-conflicts` 三個補進來了。
>
> **原本那句留著，因為現在的挑選標準要對照它才看得懂**：擋人的不是「要不要
> codebase」，是**要不要有人插話**。job 跑的是 `claude -p ... < /dev/null`
> （`worker/run-job.sh`）—— 那仍然是完整的自主迴圈，Claude 會自己寫測試、自己跑、
> 自己改，所以 `tdd` 跑得動；真正跑不動的是需要真人回答才前進的那些
> （`ask-matt`、`wizard`、`to-tickets`）。
>
> `code-review` 仍然不收，理由換了一個：CLI 內建已經有同名的 `/code-review`，
> 兩個一起裝，清單上會出現兩個一樣的名字 —— 而指令清單是**被編輯過的推薦**
> （`CONTEXT.md`），推薦裡有兩個同名項目，那份編輯就失效了。

## `skills/pptx/` 是我們自己寫的，不是上游的

**為什麼不用 Anthropic 的 pptx skill**：它在託管模型下**讀不到**。`claude setup-token`
拿到的 token 只有 `user:inference` 這個 scope，org 同步的 skill（`skills/synced/<org>_<user>/`）
在那條認證路徑下整包被忽略 —— 2026-09-23 對照實測：同一個 HOME，掛 `.credentials.json`
時 `/anthropic-skills:pptx` 可用，換成 `CLAUDE_CODE_OAUTH_TOKEN` 就不可用。

**為什麼不把 Anthropic 那份複製進來**：不行。它的 `LICENSE.txt`（服務同步的那份與
[anthropics/skills](https://github.com/anthropics/skills) 公開 repo 裡的一字不差）明文
禁止取出、保留副本、重製、衍生、散布。README 說得也直白：那四個文件 skill 是
*source-available, not open source*，放上 GitHub 是「as a reference」。**看可以，用不行。**

所以 `skills/pptx/` 是照 BD/PM 的實際情境自己寫的：用 image 裡本來就預裝的
`pptxgenjs` / `python-pptx` / `markitdown`（見 `worker/Dockerfile`），針對「把附件
做成幾頁」與「用公司範本改／填」這兩條路，加上沒有 LibreOffice 之下能做的結構性 QA
（`scripts/check.py`）。它比 Anthropic 那份窄，但窄是刻意的 —— 我們的使用者是同一家
公司的人，他們的簡報長什麼樣是已知的。

`scripts/dup_slide.py`（複製／刪除投影片）與 `scripts/check.py` 都是從零寫的。
方法（解壓改 XML、複製投影片要註冊 relationship、`text_frame.text` 會吃掉格式）是
公開的工程知識；受著作權保護的是文字與程式碼，那兩樣這裡沒有拿。

xlsx / docx / pdf **還沒有**對應的 skill。清單上那三個指令目前指向不存在的東西，
要嘛補寫、要嘛先拿掉 —— 不要讓人點了失望。

**版本釘死在 1.2.3，不追 main。** 理由同 Dockerfile 釘版本：上游更新會無聲改變
每個 job 的行為，而這裡的內容是會指導 Claude 用 Bash 的 —— 要進來就要被看過。
升級 = 一個 PR。
