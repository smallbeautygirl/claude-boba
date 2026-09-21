"""設定。憑證一律從環境變數讀，不寫進程式碼（.claude/rules/security.md）。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    observ_base_url: str = (
        "https://lighthouse-production.visionai.linkervision.ai/observ"
    )
    observ_service_id: str = "e39940ea-1fdf-4527-a3b7-c8d6334e5d2e"
    observ_timeout: float = 15.0

    # 通知裡連回 job 頁面用。
    web_base_url: str = "http://localhost:5173"

    # 共用頻道的 Workflows webhook。沒設個人 webhook 的人走這裡，
    # 用 @mention 讓當事人收到紅點通知。
    teams_channel_webhook: str = ""
    # 掛債通知要不要進頻道。開著比較符合這個產品的社交機制，
    # 但也可能讓人不好意思借。
    teams_channel_debts: bool = True

    # 允許的前端來源，逗號分隔。開發時要把區網位址加進來，
    # 否則從別台機器開的瀏覽器會被 CORS 擋下。
    # 不要圖方便寫 ["*"]（.claude/rules/security.md）。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # worker 領單的 long-poll 最長 hold 時間（秒）
    worker_poll_timeout: int = 30
    # 排隊超過這個時間無人接單即作廢（SPEC §5）
    job_queue_expiry_seconds: int = 900


settings = Settings()
