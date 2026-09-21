"""設定。憑證一律從環境變數讀，不寫進程式碼（.claude/rules/security.md）。"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://boba:boba@localhost:55432/boba"

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "boba"
    s3_secret_key: str = "boba-secret"
    s3_bucket: str = "claude-boba"
    s3_region: str = "us-east-1"

    observ_base_url: str = (
        "https://lighthouse-production.visionai.linkervision.ai/observ"
    )
    observ_service_id: str = "e39940ea-1fdf-4527-a3b7-c8d6334e5d2e"
    observ_timeout: float = 15.0

    # 允許的前端來源，逗號分隔。開發時要把區網位址加進來，
    # 否則從別台機器開的瀏覽器會被 CORS 擋下。
    # 不要圖方便寫 ["*"]（.claude/rules/security.md）。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # worker 領單的 long-poll 最長 hold 時間（秒）
    worker_poll_timeout: int = 30
    # 排隊超過這個時間無人接單即作廢（SPEC §5）
    job_queue_expiry_seconds: int = 900


settings = Settings()
