"""代跑者的長期 OAuth token 加密存放。

`.claude/rules/security.md` 紅線 2 對這種 token 有五條不可省的約束，這個模組
負責其中兩條：

- **在資料庫裡加密存放，金鑰放在資料庫外**（`hub/.env`）。這擋不住拿到整台
  主機的人，但擋得住最可能發生的那種外洩：備份外流、SQL injection、有人拿到
  psql。
- **金鑰沒設就拒絕啟動**，不要默默用明文存。

另外三條（不進 log、不寫檔、不回前端）不是這裡能保證的 —— 那要靠呼叫端不要
把 `open()` 的結果印出去或放進 response。所以這個模組**刻意不提供** repr 或
「遮罩後的值」那種東西：一旦有那個函式，就會有人在 debug 時用它。

用 Fernet（AES-128-CBC + HMAC）。不自己拼 AES —— 這種東西自己拼的失敗方式
是「看起來有加密」。
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_ENV_NAME = "TOKEN_ENCRYPTION_KEY"


class SecretsNotConfigured(RuntimeError):
    """金鑰沒設或設錯。這會讓 Hub 拒絕啟動 —— 不要退化成明文存。"""


def generate_key() -> str:
    """給人跑一次、把結果貼進 hub/.env 用的。"""
    return Fernet.generate_key().decode()


def _box() -> Fernet:
    raw = settings.token_encryption_key
    if not raw:
        raise SecretsNotConfigured(
            f"{_ENV_NAME} 沒有設。代跑者的長期 token 一定要加密存放"
            f"（.claude/rules/security.md 紅線 2），所以不設就不啟動。\n"
            f"產生一把：  .venv/bin/python -c "
            f"'from app.secrets_box import generate_key; print(generate_key())'\n"
            f"然後寫進 hub/.env 的 {_ENV_NAME}="
        )
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise SecretsNotConfigured(
            f"{_ENV_NAME} 不是合法的 Fernet 金鑰（要 44 字元的 base64）。"
            f"用 generate_key() 產生。"
        ) from exc


def check_configured() -> None:
    """啟動時叫一次。金鑰有問題就在這裡爆，不要等到有人要授權才爆。"""
    _box()


def seal(plaintext: str) -> bytes:
    return _box().encrypt(plaintext.encode())


def open_(ciphertext: bytes) -> str:
    """解回明文。

    名字後面那個底線是為了不要遮蔽內建的 `open` —— 這個模組會被 import 進
    處理檔案的地方。
    """
    try:
        return _box().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        # 換過金鑰、或資料被動過。不要吞掉 —— 悄悄回 None 的話，呼叫端會以為
        # 這個代跑者沒授權過，然後叫他重新授權，而舊的 token 還在外面有效。
        raise SecretsNotConfigured(
            "解不開已存的 token。金鑰換過了嗎？"
            "換金鑰需要重新加密既有資料，不能只改 .env。"
        ) from exc
