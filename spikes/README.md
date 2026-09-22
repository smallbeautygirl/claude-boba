# 驗證腳本

`SPEC.md` §11 的 spike 引用這裡的東西。它們**不是 scratch** ——
`playground/` 才是，那整個目錄被 gitignore 了。

放在版控裡的理由：§11 的每一條 spike 都寫著「實測結果是什麼」，而那些結論
只有在別人能重跑的時候才算數。腳本不進版控的話，SPEC 就變成一份「相信我」
的文件。

| 腳本 | 對應 | 會不會產生副作用 |
|---|---|---|
| `spike8-oauth-token.sh` | §11 #8 長期 OAuth token 當環境變數 | 會真的跑 job、花錢（實測約 US$0.05）。需要 `worker/.env` 裡有 `CLAUDE_CODE_OAUTH_TOKEN` |
| `spike9-setup-token-pty.py` | §11 #9 用 pty 驅動 `claude setup-token` | **不會**。用 scratch HOME、刻意不完成授權，跑完不留任何憑證 |

跑之前先確認 image 與 network 在：

```bash
docker image inspect claude-boba-worker:2.1.278 >/dev/null && echo ok
docker network inspect claude-boba-worker_jobnet >/dev/null && echo ok
```
