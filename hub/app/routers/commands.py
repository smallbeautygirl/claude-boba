"""推薦指令清單。

刻意是**推薦**而不是完整列舉：CLI 內建 30 幾個 slash command，全丟給使用者
只會讓人不知所措。這份清單由人挑選、可以 code review，並標註適合誰。

清單是靜態的，所以它可能與 CLI 實際提供的指令漂移 —— 代價是某個推薦的指令
在升版後失效，而那會在那個 job 上明確報錯，不是無聲失敗。用動態列舉的代價
（每次啟動燒一次 API 呼叫、還要解析自然語言回覆）不划算。
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/commands", tags=["commands"])

_CATALOG = Path(__file__).resolve().parent.parent / "data" / "commands.json"


@cache
def _catalog() -> dict:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


@router.get("")
async def list_commands() -> dict:
    return _catalog()
