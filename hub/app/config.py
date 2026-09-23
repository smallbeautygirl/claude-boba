"""設定。憑證一律從環境變數讀，不寫進程式碼（.claude/rules/security.md）。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 代跑者長期 token 的加密金鑰（security.md 紅線 2：金鑰放在資料庫外）。
    # 預設空字串而不是隨便給一把 —— 給預設值等於讓人在不知情的狀況下
    # 用一把全世界都知道的金鑰跑正式環境。沒設就拒絕啟動。
    token_encryption_key: str = ""

    database_url: str = "postgresql+asyncpg://boba:boba@localhost:55432/boba"

    s3_endpoint: str = "http://localhost:9000"
    # 預簽 URL 用的 endpoint。瀏覽器與 worker 都不在 Hub 的 localhost 上，
    # 所以簽章必須用對外可達的位址。
    #
    # ⚠️ 不能簽完再把網址裡的 host 換掉 —— SigV4 的簽章涵蓋 Host header，
    # 換了就驗不過。要用對外 endpoint 重新簽一份。
    s3_public_endpoint: str = ""
    s3_access_key: str = "boba"
    s3_secret_key: str = "boba-secret"
    s3_bucket: str = "claude-boba"
    s3_region: str = "us-east-1"

    # 認證來源。**兩個都沒有預設值，沒設就拒絕啟動**（main.py）。
    #
    # 2026-09-23 從寫死的預設值改成必填。它們不是憑證，但它們是**某個組織的內部
    # 位址與服務註冊編號** —— 寫在程式裡等於這個 repo 一旦公開，就連同「這個端點
    # 在哪、怎麼打、它的 service id 是什麼」一起發布。位址本身擋不住任何人，
    # 但也沒有任何理由由程式碼提供。
    #
    # 不給預設值的第二個理由跟 admin_emails 同一條：有預設值的設定會被人當成
    # 「不用管」，然後某天有人在另一個環境跑起來，默默打到正式的認證服務。
    observ_base_url: str = ""
    observ_service_id: str = ""
    observ_timeout: float = 15.0

    # 通知裡連回 job 頁面用。
    web_base_url: str = "http://localhost:5173"

    # 共用頻道的 Workflows webhook。沒設個人 webhook 的人走這裡，
    # 用 @mention 讓當事人收到紅點通知。
    teams_channel_webhook: str = ""
    # 那個頻道叫什麼，原字串直接顯示在通知設定頁上。跟 webhook 放在一起是
    # 刻意的：分開放的話，webhook 改指到別的頻道時，畫面上的名字不會跟著變。
    teams_channel_name: str = ""
    # 掛債通知要不要進頻道。開著比較符合這個產品的社交機制，
    # 但也可能讓人不好意思借。
    teams_channel_debts: bool = True

    # 管理者的 email 清單，逗號分隔。名單改動的頻率是「幾個月一次」，
    # 所以不加資料庫欄位、不做管理介面，改 env 重啟就好。
    #
    # **沒設就是沒有人是 admin，這是刻意的，不是還沒做完。** 不要改成
    # 「沒設就都是」或「第一個註冊的是」那種方便做法 —— 那會讓一個空的
    # 設定檔變成權限漏洞。
    admin_emails: str = ""

    # MinIO console 的網址（跟 S3_PUBLIC_ENDPOINT 不同埠）。不從那個位址推算
    # 埠號 —— 那是猜的。沒設就代表這個站台沒開 console，前端整段不顯示：
    # 一個連不上的連結比沒有連結更糟。
    s3_console_url: str = ""

    # 允許的前端來源，逗號分隔。開發時要把區網位址加進來，
    # 否則從別台機器開的瀏覽器會被 CORS 擋下。
    # 不要圖方便寫 ["*"]（.claude/rules/security.md）。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 領單主機的共用 token。**沒設就拒絕領單**，不給預設值 ——
    # 給了等於讓一個空設定檔變成任何人都能領走別人 job（連同一年期 OAuth token）
    # 的漏洞。這不再由網頁產生：worker 的身分是「我是這台主機」，
    # 不是「我是某位代跑者」（SPEC §4.12）。
    worker_shared_token: str = ""

    # worker 領單的 long-poll 最長 hold 時間（秒）
    worker_poll_timeout: int = 30
    # 排隊超過這個時間無人接單即作廢（SPEC §5）
    job_queue_expiry_seconds: int = 900
    # 執行中的 job 超過這個時間沒有任何 worker 回報，hub 就替它結案為 failed
    # （app/orphans.py）。心跳是每 3 秒一次，所以 5 分鐘不是「快到了」而是「死了」；
    # 留這麼長是給大附件下載（領單到第一次心跳之間）與 hub 自己重啟的餘裕。
    job_heartbeat_timeout_seconds: int = 300

    # 管理頁「累計花費」的台幣粗估用的匯率來源（hub/app/fxrate.py）。
    # **設成空字串就整個關掉** —— 那時管理頁只顯示美金，並說明為什麼沒有台幣，
    # 不會退化成一個寫死的係數。粗估可以粗，不可以來路不明。
    fx_api_url: str = "https://api.finmindtrade.com/api/v4/data"
    # 逾時給得短：這是管理頁上一個可有可無的附註，不值得讓整頁等它。
    fx_timeout: float = 5.0

    # 授權時自動去 Anthropic 反查這個 Claude 帳號是誰（app/claude_profile.py）。
    #
    # **預設關掉，因為它現在必定失敗。** 2026-09-23 實測：`claude setup-token`
    # 產的 token 打 `/api/oauth/profile` 回 **403**（SPEC §11 spike #12）。
    # 開著等於每次授權都多一個保證失敗的往返，還會在帳號卡上留下一行誤導的字。
    #
    # 留著這個開關而不是把整段程式刪掉：token 的 scope 是 Anthropic 那邊決定的，
    # 哪天變了，這裡打開就會動。出借頁那顆「查一次」**不受這個開關影響** ——
    # 手動按一次是發現「哪天變了」的唯一途徑。
    claude_profile_lookup: bool = False

    @property
    def admin_email_set(self) -> frozenset[str]:
        """小寫化 + trim 之後的 admin 清單。

        人手維護的清單一定會有大小寫與前後空白，比對前正規化 ——
        不然「設了卻不生效」會變成一個很難查的問題。
        """
        return frozenset(
            e.strip().lower() for e in self.admin_emails.split(",") if e.strip()
        )


settings = Settings()
