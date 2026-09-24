#!/usr/bin/env python3
"""查 Observ 事件紀錄：截圖、VLM 回答、審核狀態、middleware 的二次驗證與轉發結果。

**這支腳本是 job 容器裡唯一碰得到 Observ token 的東西。** token 從環境變數讀，
只出現在送出去的 HTTP 請求裡；stdout、stderr、檔名、summary.md 都不會有它。
job 的整條 stream-json 會被 hub 存進資料庫並重播給瀏覽器，所以任何把 token
印出來或組進指令列的做法，都等於把委託者 72 小時的 Observ 身分寫進資料庫。
SKILL.md 因此規定：Claude 只能透過這支腳本查，不得自己組網址。

只用標準庫（容器裡沒有 requests / httpx）。只做 GET，不改任何東西。

環境變數（由 hub 派單時注入，SKILL.md 有對照）：
  OBSERV_TOKEN         委託者登入 boba 的 Observ access token（Keycloak JWT，72 小時）
  OBSERV_BASE_URL      例 https://lighthouse-production.visionai.linkervision.ai/observ
  OBSERV_SERVICE_ID    Observ 的 service id（站台一個，大家一樣）
  MIDDLEWARE_BASE_URL  例 https://192.168.80.120:8443（不含路徑；腳本自己接 /observ/apiserver）
  MIDDLEWARE_CA_FILE   選填：middleware 的 TLS 憑證 PEM。預設用同目錄的 middleware-ca.pem。

子指令（輸出一律是 stdout 上的一份 JSON，並在 --out 目錄寫 summary.md 與截圖）：
  record   <event_record_id>...                 一筆或多筆事件紀錄
  tracking <tracking_id>...                     middleware 的「一次發生」（task_id-紀錄id）
  type     <event_type_id>... --start --end     某事件類型在時間窗內的紀錄
  name     "<客戶端事件名稱>" --start --end       客戶那邊看到的名稱（翻譯過的）在時間窗內的紀錄
  vlm-labels <event_type_id>                    該事件類型 VLM 回答的中文對照

共同選項：--limit N（預設 20、上限 40）、--no-images、--out DIR（預設 .）、
          --vendor cht|iisi、--outcome <轉發結果>（type / name 專用；有帶就從 middleware 那邊起查）

資料來源（2026-09-24 起）：先查 Observ；**Observ 查不到但 middleware 有的紀錄，改用 middleware
的內容補出來**，並在 notes 與每筆的 `data_source` 講明。實例：1111955 在 Observ 的查詢 API 回 0 筆，
middleware 卻有完整紀錄、截圖也抓得到。Observ 歷史頁連結只在 Observ 真的查得到時才給。
每筆都附 `middleware` 區塊（收到時間、一次發生的起訖、轉發與二次驗證、middleware UI 連結），
給 PM 看比較細的資訊。

結束碼：0 正常；2 參數錯；3 Observ 登入過期（stdout 會有固定訊息，照抄給使用者）；
        4 上游錯誤（Observ / middleware 回了非 2xx 或連不上）。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = Path(__file__).resolve().parent

DEFAULT_LIMIT = 20
# 50 個檔的 job 上限扣掉 summary.md 與餘量。超過就列總數請使用者縮範圍，不要偷偷截。
HARD_LIMIT = 40
# Observ 一頁最多 50；middleware 的 event-flow 一頁最多 500、時間窗最長 7 天。
OBSERV_PAGE = 50
FLOW_PAGE = 500
FLOW_MAX_PAGES = 10
FLOW_WINDOW_MAX = timedelta(days=7)
# 一筆紀錄要在 event-flow 裡找回自己時用的窗（發生時間 ± 這麼多）。
FLOW_NEIGHBOURHOOD = timedelta(minutes=2)
TIMEOUT = 30
# middleware 合成的「結束」訊息用 epoch 毫秒當 event_record_id（13 位數）；真的紀錄 id
# 目前是 7 位數。超過這條線的當合成的，不拿去 Observ 查。
SYNTHETIC_ID_FLOOR = 10**12

VENDOR_LABELS = {"cht": "中華", "iisi": "資拓"}
# 中華排前面：PM 目前比較關心中華（2026-09-24）。
VENDOR_ORDER = ("cht", "iisi")
# 轉發結果、處理階段、收到管道的中文與原因，**照抄 middleware UI 的用詞**
# （visionai_middleware/app/ui_static/js/flow/meta.js）。PM 平常看的是那個介面，
# 兩邊講同一件事要用同一個字。
OUTCOME_LABELS = {
    "delivered": "已發送",
    "gps_filtered": "被 GPS 過濾",
    "unconfigured": "未設定白名單",
    "awaiting_confirmation": "等待人工確認",
    "gate_blocked": "被模型擋下",
    "verification_error": "驗證無法完成",
    "bus_duplicate": "重複事件",
    "not_whitelisted": "白名單未勾選",
}
OUTCOME_WHY = {
    "delivered": "通過所有關卡，並已依白名單發送給客戶。",
    "gps_filtered": "事件座標落在設定的 GPS 排除範圍內，不視為一次真實事件。",
    "unconfigured": "middleware 沒有這個事件型別的設定，因此無從判斷要發送給誰。",
    "awaiting_confirmation": "這是需要人工確認的事件型別，尚未取得確認結果，因此暫不發送。",
    "gate_blocked": "二次驗證模型看過畫面後認為不符合，依設定不發送。",
    "verification_error": "模型沒能取得或判讀畫面（抓圖失敗、逾時等），依「失敗即不發送」處理。",
    "bus_duplicate": "公車路線上另一台車在既有事件附近再次偵測到同一個狀況，屬於重複通報。",
    "not_whitelisted": "通過所有關卡，但沒有任何一家客戶的白名單勾選這個事件型別。",
}
STATUS_LABELS = {"TBC": "待確認", "Confirmed": "已確認", "False Alarm": "誤報"}
MESSAGE_STATUS_LABELS = {
    "start": "開始",
    "in progress": "進行中",
    "end": "結束",
    "skip": "略過",
}
SOURCE_LABELS = {
    "observ_socket": "Observ WS 即時推送",
    "observ_polling": "Observ 輪詢",
    "repeat_socket": "Raw socket",
}
# 一筆事件在 middleware 裡走到的最遠一關，依序。
STAGE_LABELS = {
    "received": "接收",
    "geo_passed": "通過 GPS",
    "configured": "已設定白名單",
    "verified": "通過驗證",
    "delivered": "已發送",
}
# middleware 存的時間（created_at 等）沒帶時區，是 middleware 主機的本地時間。
MIDDLEWARE_TZ = "Asia/Taipei"

EXPIRED_MESSAGE = "Observ 登入已過期，請重新登入 boba 後用「接著問」重試。"


class Expired(Exception):
    """Observ 或 middleware 回 401。token 死了，後面什麼都不用試。"""


class Upstream(Exception):
    def __init__(self, where: str, status: int | None, detail: str) -> None:
        super().__init__(f"{where} {status}: {detail}")
        self.where, self.status, self.detail = where, status, detail


# --- HTTP ----------------------------------------------------------------------


class _Env:
    def __init__(self) -> None:
        self.token = os.environ.get("OBSERV_TOKEN", "").strip()
        self.observ = os.environ.get("OBSERV_BASE_URL", "").rstrip("/")
        self.service_id = os.environ.get("OBSERV_SERVICE_ID", "").strip()
        self.middleware = os.environ.get("MIDDLEWARE_BASE_URL", "").rstrip("/")
        self.ca_file = os.environ.get("MIDDLEWARE_CA_FILE") or str(
            HERE / "middleware-ca.pem"
        )
        missing = [
            n
            for n, v in (
                ("OBSERV_TOKEN", self.token),
                ("OBSERV_BASE_URL", self.observ),
                ("OBSERV_SERVICE_ID", self.service_id),
            )
            if not v
        ]
        if missing:
            _die(
                2,
                "缺少環境變數 "
                + "、".join(missing)
                + "。這個 job 沒有帶 Observ 身分 —— 提交時 prompt 要以 "
                "/observ-event-lookup 開頭，hub 才會把登入的 Observ token 注入。",
            )

    @property
    def base_domain(self) -> str:
        """Observ 的網域（去掉 /observ），歷史頁連結用。"""
        return re.sub(r"/observ$", "", self.observ)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """對 middleware 用釘住的自簽憑證驗證。

    兩台 middleware 主機（.80.136 與 .80.120）都用同一張 CN=localhost 的自簽憑證，
    有效到 2036。**不用 verify=False**：那會讓任何擋在中間的人拿到 token。
    憑證換了的話 pin 會失敗，腳本退回只查 Observ，並在 notes 裡講。
    """

    pinned_ctx: ssl.SSLContext

    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)
        self.sock = self.pinned_ctx.wrap_socket(self.sock, server_hostname="localhost")


class Client:
    def __init__(self, env: _Env) -> None:
        self.env = env
        self.notes: list[str] = []
        self.middleware_ok = bool(env.middleware)
        self._mw_opener: urllib.request.OpenerDirector | None = None
        if env.middleware:
            try:
                ctx = ssl.create_default_context(cafile=env.ca_file)
            except (OSError, ssl.SSLError) as exc:
                self.middleware_ok = False
                self.notes.append(
                    f"middleware 憑證檔讀不到（{exc}），這次只查 Observ。"
                )
            else:
                conn_cls = type("PinnedHTTPS", (_PinnedHTTPS,), {"pinned_ctx": ctx})

                class Handler(urllib.request.HTTPSHandler):
                    def https_open(self, req):  # type: ignore[override]
                        return self.do_open(conn_cls, req)

                self._mw_opener = urllib.request.build_opener(Handler())
        else:
            self.notes.append(
                "沒有 MIDDLEWARE_BASE_URL，這次只查 Observ，沒有轉發結果。"
            )

    # Observ apiserver 的資料端點認 x-service-id；middleware 認 X-Service-Id。
    # 兩邊 header 名稱大小寫不同、送錯只會 401，長得跟 token 過期一樣。
    def observ_get(self, path: str, params: list[tuple[str, Any]]) -> Any:
        url = f"{self.env.observ}/apiserver{path}?{urllib.parse.urlencode(params)}"
        return self._get_json(
            url,
            {
                "Authorization": f"Bearer {self.env.token}",
                "x-service-id": self.env.service_id,
            },
            where="Observ",
        )

    def middleware_get(self, path: str, params: list[tuple[str, Any]]) -> Any:
        if not self.middleware_ok:
            raise Upstream("middleware", None, "unavailable")
        url = f"{self.env.middleware}/observ/apiserver{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return self._get_json(
                url,
                {
                    "Authorization": f"Bearer {self.env.token}",
                    "X-Service-Id": self.env.service_id,
                },
                where="middleware",
                opener=self._mw_opener,
            )
        except Upstream as exc:
            if exc.status is None:
                # 連不上或憑證不符：這次不再打 middleware，Observ 那邊照查。
                self.middleware_ok = False
                self.notes.append(
                    f"middleware 連不上（{exc.detail}），轉發結果與二次驗證這次查不到。"
                )
            raise

    def download(self, url: str) -> bytes:
        """抓截圖。media-provider 只認 query 上的 auth_token，Bearer header 會 401。"""
        sep = "&" if "?" in url else "?"
        full = (
            f"{url}{sep}"
            f"auth_token={urllib.parse.quote(self.env.token)}"
            f"&service_id={urllib.parse.quote(self.env.service_id)}"
        )
        req = urllib.request.Request(full)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Expired from None
            # 不把 exc 原樣往外丟：它的 url 屬性含 token。
            raise Upstream("截圖", exc.code, "download failed") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise Upstream("截圖", None, type(exc).__name__) from None

    def _get_json(
        self,
        url: str,
        headers: dict[str, str],
        *,
        where: str,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> Any:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", **headers}
        )
        try:
            with (opener or urllib.request.build_opener()).open(
                req, timeout=TIMEOUT
            ) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Expired from None
            body = exc.read().decode("utf-8", errors="replace")[:300]
            raise Upstream(where, exc.code, body) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise Upstream(where, None, str(reason)[:200]) from None


# --- 時間 -------------------------------------------------------------------------


def parse_time(text: str) -> datetime:
    """接 ISO 8601（含 Z 或 +08:00）。沒帶時區當台北時間 —— 使用者講的都是台北時間。"""
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        _die(2, f"時間格式看不懂：{text!r}，請用 2026-07-24T09:47:00+08:00 這種寫法")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz("Asia/Taipei"))
    return dt


def _tz(name: str | None):
    try:
        return ZoneInfo(name or "Asia/Taipei")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Taipei")


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_str(ts: str | None, tz_name: str | None) -> str | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_tz(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")


def file_stamp(ts: str | None, tz_name: str | None) -> str:
    if not ts:
        return "unknown-time"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return "unknown-time"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_tz(tz_name)).strftime("%Y%m%d-%H%M%S")


# --- 查詢 -------------------------------------------------------------------------


def observ_records_by_ids(client: Client, ids: list[int]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(ids), OBSERV_PAGE):
        chunk = ids[i : i + OBSERV_PAGE]
        # ids 要重複帶（ids=1&ids=2）；逗號分隔會 422。
        data = client.observ_get(
            "/v2/events/record", [("ids", x) for x in chunk] + [("size", OBSERV_PAGE)]
        )
        out.extend(data.get("results") or [])
    return out


def observ_records_by_type(
    client: Client, type_ids: list[int], start: datetime, end: datetime, want: int
) -> tuple[list[dict], int]:
    """回 (紀錄, 總數)。只抓到 want 筆，總數另外回報讓使用者知道還有多少。"""
    out: list[dict] = []
    total = 0
    page = 1
    while len(out) < want:
        data = client.observ_get(
            "/v2/events/record",
            [("event_type_ids", t) for t in type_ids]
            + [
                ("start_time", iso_utc(start)),
                ("end_time", iso_utc(end)),
                ("page", page),
                ("size", OBSERV_PAGE),
                ("order", "-created_at"),
            ],
        )
        total = int(data.get("total") or 0)
        results = data.get("results") or []
        out.extend(results)
        if len(results) < OBSERV_PAGE or page >= int(data.get("total_pages") or 1):
            break
        page += 1
    return out[:want], total


def flow_rows(
    client: Client,
    start: datetime,
    end: datetime,
    *,
    type_ids: list[int] | None = None,
    vendor: str | None = None,
    outcome: str | None = None,
    max_pages: int = FLOW_MAX_PAGES,
    anchors_only: bool = True,
) -> list[dict]:
    """middleware 的 event-flow 攤平成一列一筆。

    列清單時只取每次發生的 **anchor**（真正的開始那一筆）：members 裡的「結束」訊息
    帶的 event_record_id 是合成的（epoch 毫秒），Observ 查不到，列出來只會變成
    「找不到這筆」。要在 event-flow 裡找回**某一筆**時才連 members 一起看。
    """
    if not client.middleware_ok:
        return []
    if end - start > FLOW_WINDOW_MAX:
        client.notes.append(
            "middleware 的轉發結果最多查 7 天，時間窗超過的部分沒有轉發結果。"
        )
    rows: dict[int, dict] = {}
    page = 1
    while page <= max_pages:
        params: list[tuple[str, Any]] = [
            ("start_time", iso_utc(start)),
            ("end_time", iso_utc(end)),
            ("page", page),
            ("size", FLOW_PAGE),
        ]
        params += [("event_type_ids", t) for t in (type_ids or [])]
        if vendor:
            params.append(("vendors", vendor))
        if outcome:
            params.append(("outcomes", outcome))
        try:
            data = client.middleware_get("/pm/event-flow", params)
        except Upstream:
            return list(rows.values())
        results = data.get("results") or []
        for occ in results:
            members = [] if anchors_only else list(occ.get("members") or [])
            for row in [occ.get("anchor")] + members:
                if not row:
                    continue
                rid = row.get("event_record_id")
                if (
                    isinstance(rid, int)
                    and rid < SYNTHETIC_ID_FLOOR
                    and rid not in rows
                ):
                    row = dict(row)
                    row["_tracking_id"] = occ.get("tracking_id")
                    row["_occurrence"] = {
                        k: occ.get(k)
                        for k in (
                            "started_at",
                            "ended_at",
                            "duration_seconds",
                            "statuses",
                            "message_count",
                        )
                    }
                    rows[rid] = row
        if len(results) < FLOW_PAGE or page >= int(data.get("total_pages") or 1):
            break
        page += 1
    return list(rows.values())


def processed_log(client: Client, record_id: int) -> dict | None:
    if not client.middleware_ok:
        return None
    try:
        data = client.middleware_get(
            "/v2/processed-event-logs", [("event_record_id", record_id), ("size", 5)]
        )
    except Upstream as exc:
        if exc.status == 403:
            client.notes.append(
                "你的 Observ 身分在 middleware 是 vendor 使用者，看不到二次驗證明細。"
            )
        return None
    # 只收同一個紀錄 id 的列 —— 不信任上游的篩選一定生效。
    results = [
        r for r in (data.get("results") or []) if r.get("event_record_id") == record_id
    ]
    # 同一個紀錄 id 可能有 start / end 兩列，取 start（有二次驗證答案的那一列）。
    results.sort(key=lambda r: (r.get("status") != "start", r.get("id") or 0))
    return results[0] if results else None


_LABEL_CACHE: dict[int, dict] = {}


def vlm_labels(client: Client, type_id: int) -> dict:
    if type_id in _LABEL_CACHE:
        return _LABEL_CACHE[type_id]
    labels: dict = {}
    if client.middleware_ok:
        try:
            data = client.middleware_get(f"/pm/annotations/vlm-labels/{type_id}", [])
            labels = data.get("keys") or {}
        except Upstream:
            labels = {}
    _LABEL_CACHE[type_id] = labels
    return labels


def translate_vlms(vlms: Any, labels: dict) -> list[dict]:
    """把 VLM 回答攤成 [{question, question_zh, answer, answer_zh}]。"""
    out: list[dict] = []
    for block in vlms or []:
        for item in block.get("results") or []:
            key = item.get("key")
            val = item.get("val")
            meta = labels.get(key) or {}
            answers = meta.get("answers") or {}
            vals = val if isinstance(val, list) else [val]
            zh = [answers.get(str(v), None) for v in vals]
            out.append(
                {
                    "template": block.get("vlm_template_name"),
                    "question": key,
                    "question_zh": meta.get("question"),
                    "answer": val,
                    "answer_zh": zh if any(zh) else None,
                }
            )
    return out


def translated_type_ids(client: Client, name: str) -> list[int]:
    """客戶端事件名稱 → Observ 事件類型 id。

    正式站的 middleware 目前沒有部署 /v2/event-translate（2026-09-24 兩台都 404），
    所以這裡試一次、404 就回空，讓呼叫端改用 event-flow 掃名稱。
    """
    if not client.middleware_ok:
        return []
    try:
        data = client.middleware_get(
            "/v2/event-translate", [("new_event_name", name), ("limit", 200)]
        )
    except Upstream:
        return []
    items = (
        data
        if isinstance(data, list)
        else (data.get("results") or data.get("items") or [])
    )
    ids: list[int] = []
    for it in items:
        eid = it.get("event_id") or it.get("event_type_id")
        if isinstance(eid, int) and eid not in ids:
            ids.append(eid)
    return ids


def record_from_middleware(row: dict) -> dict:
    """把 middleware 的一列（processed-event-logs 或 event-flow）轉成 Observ 紀錄的形狀。

    Observ 查不到、middleware 有的時候用（1111955 就是這樣）。欄位名兩邊大多一樣；
    座標 middleware 拆成 lat / lon 兩欄。審核狀態 middleware 記的是收到當下的值，
    之後在 Observ 上改過的不一定跟上 —— describe 會把來源標出來。
    """
    rec = {
        k: row.get(k)
        for k in (
            "event_record_id",
            "task_id",
            "event_type_id",
            "event_name",
            "event_description",
            "event_record_status",
            "timestamp",
            "image_url",
            "video_url",
            "video_time",
            "location_id",
            "camera_id",
            "timezone",
            "vlms",
            "detected_objects",
            "note",
        )
    }
    if not isinstance(row.get("coordinates"), dict):
        lat, lon = row.get("coordinates_lat"), row.get("coordinates_lon")
        rec["coordinates"] = (
            {"latitude": lat, "longitude": lon} if lat is not None else None
        )
    else:
        rec["coordinates"] = row["coordinates"]
    rec["timezone"] = rec.get("timezone") or MIDDLEWARE_TZ
    return rec


def _parse_any(ts: str | None, naive_tz: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz(naive_tz))


def _local(dt: datetime | None, tz_name: str | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(_tz(tz_name)).strftime("%Y-%m-%d %H:%M:%S %Z")


def middleware_block(
    client: Client, record: dict, flow: dict | None, plog: dict | None
) -> dict | None:
    """給 PM 看的 middleware 細節。middleware 完全沒有這筆時回 None。"""
    if not flow and not plog:
        return None
    flow, plog = flow or {}, plog or {}
    tz = record.get("timezone") or MIDDLEWARE_TZ
    happened = _parse_any(record.get("timestamp"), "UTC")
    received = _parse_any(
        plog.get("created_at") or flow.get("created_at"), MIDDLEWARE_TZ
    )
    delay = (
        round((received - happened).total_seconds(), 1)
        if happened and received
        else None
    )
    occ = flow.get("_occurrence") or {}
    source = plog.get("source") or flow.get("source")
    status = plog.get("status") or flow.get("status")
    stage = flow.get("stage")
    type_id = record.get("event_type_id")
    ui = client.env.middleware
    return {
        "received_at_local": _local(received, tz),
        "delay_seconds": delay,
        "source": source,
        "source_zh": SOURCE_LABELS.get(source or "", source),
        "message_status": status,
        "message_status_zh": MESSAGE_STATUS_LABELS.get(status or "", status),
        "stage": stage,
        "stage_zh": STAGE_LABELS.get(stage or "", stage),
        "occurrence": {
            "tracking_id": flow.get("_tracking_id") or plog.get("tracking_id"),
            "started_at_local": _local(_parse_any(occ.get("started_at"), "UTC"), tz),
            "ended_at_local": _local(_parse_any(occ.get("ended_at"), "UTC"), tz),
            "duration_seconds": occ.get("duration_seconds"),
            "statuses": occ.get("statuses"),
            "message_count": occ.get("message_count"),
        }
        if occ
        else None,
        "log_id": plog.get("id") or flow.get("id"),
        # middleware UI 的歷史事件頁只吃 event_type（與 outcome）這種 hash 參數，
        # 沒有單筆事件的深連結；頁面預設只顯示最近 24 小時。
        "ui_url": (
            f"{ui}/middleware-ui/#/event-flow?event_type={type_id}"
            if ui and isinstance(type_id, int)
            else None
        ),
    }


# --- 組一筆的完整交代 ---------------------------------------------------------------


def describe(
    client: Client,
    record: dict,
    flow: dict | None,
    plog: dict | None,
    data_source: str = "observ",
) -> dict:
    rid = record.get("event_record_id")
    tz = record.get("timezone")
    type_id = record.get("event_type_id")
    labels = vlm_labels(client, type_id) if isinstance(type_id, int) else {}

    gate = (flow or {}).get("gate") or {}
    delivered = (flow or {}).get("delivered_to") or {}
    whitelisted = (flow or {}).get("whitelisted_for") or {}
    delivered_to = [VENDOR_LABELS[v] for v in VENDOR_ORDER if delivered.get(v)]
    whitelisted_for = [VENDOR_LABELS[v] for v in VENDOR_ORDER if whitelisted.get(v)]
    outcome = (flow or {}).get("outcome")

    verification: dict | None = None
    if plog or gate:
        source = gate.get("verification_source") or (plog or {}).get(
            "verification_source"
        )
        answer = gate.get("answer") or (plog or {}).get("gemma_answer")
        passed = gate.get("passed")
        verification = {
            "gated": gate.get("is_gated"),
            "passed": passed,
            "passed_zh": {True: "通過", False: "沒通過", None: "沒有判定"}.get(passed),
            "source": source,
            "answer": answer,
            "ensemble_decision": gate.get(
                "ensemble_decision", (plog or {}).get("ensemble_decision")
            ),
            "failure_code": gate.get("failure_code"),
        }

    status = record.get("event_record_status")
    return {
        "event_record_id": rid,
        # observ = Observ 查得到；middleware = Observ 查不到，內容取自 middleware。
        "data_source": data_source,
        "tracking_id": (flow or {}).get("_tracking_id")
        or (plog or {}).get("tracking_id"),
        "event_type_id": type_id,
        "event_name": record.get("event_name"),
        "customer_event_name": (flow or {}).get("new_event_name"),
        "customer_event_id": (flow or {}).get("new_event_id"),
        "timestamp_utc": record.get("timestamp"),
        "timestamp_local": local_str(record.get("timestamp"), tz),
        "timezone": tz,
        "review_status": status,
        "review_status_zh": STATUS_LABELS.get(status or "", status),
        "task_id": record.get("task_id"),
        "camera_id": record.get("camera_id"),
        "cctv_id": (flow or {}).get("cctv_id") or (plog or {}).get("cctv_id"),
        "location_id": record.get("location_id"),
        "coordinates": record.get("coordinates"),
        "note": record.get("note"),
        "vlm": translate_vlms(record.get("vlms"), labels),
        "detected_objects": len(record.get("detected_objects") or []),
        "forwarding": {
            "outcome": outcome,
            "outcome_zh": OUTCOME_LABELS.get(outcome or "", outcome),
            "outcome_why": OUTCOME_WHY.get(outcome or ""),
            "delivered_to": delivered_to,
            "whitelisted_for": whitelisted_for,
            "annotation": (flow or {}).get("annotation"),
        }
        if flow
        else None,
        "verification": verification,
        "middleware": middleware_block(client, record, flow, plog),
        # 只在 Observ 真的查得到時給：查不到的紀錄，那一頁開起來是空的。
        "observ_url": (
            f"{client.env.base_domain}/observ/history?eventLogModalId={rid}"
            if data_source == "observ"
            else None
        ),
        "video_url": record.get("video_url") or None,
        "_image_urls": _image_urls(record),
    }


def _image_urls(record: dict) -> list[str]:
    urls: list[str] = []
    main = record.get("image_url")
    if main:
        urls.append(main)
    for u in record.get("image_urls") or []:
        if u and u not in urls:
            urls.append(u)
    return urls


def save_images(client: Client, item: dict, out_dir: Path) -> list[str]:
    files: list[str] = []
    stamp = file_stamp(item["timestamp_utc"], item["timezone"])
    for n, url in enumerate(item.pop("_image_urls", []), start=1):
        suffix = "" if n == 1 else f"_{n}"
        name = f"event_{item['event_record_id']}_{stamp}{suffix}.jpg"
        try:
            data = client.download(url)
        except Upstream as exc:
            client.notes.append(
                f"紀錄 {item['event_record_id']} 的截圖抓不到（{exc.where} {exc.status}）。"
            )
            continue
        (out_dir / name).write_bytes(data)
        files.append(name)
    return files


# --- summary.md ---------------------------------------------------------------------


def _yesno(v: Any) -> str:
    return {True: "是", False: "否"}.get(v, "—")


def write_summary(
    path: Path, query: dict, items: list[dict], total: int, notes: list[str]
) -> None:
    lines = ["# Observ 事件查詢結果", ""]
    lines.append(f"- 查詢：`{json.dumps(query, ensure_ascii=False)}`")
    lines.append(
        f"- 命中 {total} 筆，這裡列 {len(items)} 筆"
        + ("（已達上限，請縮小範圍）" if total > len(items) else "")
    )
    for n in notes:
        lines.append(f"- ⚠️ {n}")
    lines.append("")
    for it in items:
        fw = it.get("forwarding") or {}
        vf = it.get("verification") or {}
        lines.append(f"## 事件紀錄 {it['event_record_id']}")
        lines.append("")
        if it.get("observ_url"):
            lines.append(f"- **Observ 事件頁**：{it['observ_url']}")
        if it.get("data_source") == "middleware":
            lines.append(
                "- ⚠️ **Observ 查不到這筆**，以下內容取自 middleware"
                "（審核狀態是 middleware 收到當下的值）。"
            )
        lines.append(
            f"- 發生時間：{it.get('timestamp_local') or it.get('timestamp_utc')}"
        )
        lines.append(f"- 事件類型：{it.get('event_type_id')}「{it.get('event_name')}」")
        if it.get("customer_event_name"):
            lines.append(
                f"- 客戶端事件名稱：「{it['customer_event_name']}」（客戶端類型 {it.get('customer_event_id')}）"
            )
        lines.append(
            f"- 攝影機：camera {it.get('camera_id')}"
            + (f"，cctv {it['cctv_id']}" if it.get("cctv_id") else "")
            + f"，地點 {it.get('location_id')}"
        )
        lines.append(f"- 審核狀態：{it.get('review_status_zh')}")
        if it.get("tracking_id"):
            lines.append(f"- 一次發生（tracking_id）：`{it['tracking_id']}`")
        if fw:
            deliv = "、".join(fw.get("delivered_to") or []) or "沒有"
            lines.append(
                f"- 轉發結果：{fw.get('outcome_zh')}；轉給：{deliv}"
                + (
                    f"；名單上有：{'、'.join(fw['whitelisted_for'])}"
                    if fw.get("whitelisted_for")
                    else ""
                )
            )
            if fw.get("outcome_why"):
                lines.append(f"  - 原因：{fw['outcome_why']}")
        else:
            lines.append("- 轉發結果：（middleware 沒有這筆，或這次查不到）")
        if vf:
            lines.append(
                f"- 二次驗證：{vf.get('passed_zh')}（{vf.get('source') or '—'}，回答 {vf.get('answer')!r}）"
            )
        if it.get("vlm"):
            lines.append("- VLM 回答：")
            for v in it["vlm"]:
                q = v.get("question_zh") or v.get("question")
                a = v.get("answer_zh") or v.get("answer")
                if isinstance(a, list):
                    a = "、".join(str(x) for x in a)
                lines.append(f"  - {q}：{a}")
        if it.get("files"):
            lines.append("- 截圖：" + "、".join(f"`{f}`" for f in it["files"]))
        if it.get("video_url"):
            lines.append("- 影片片段：有（沒下載，Observ 歷史頁可看）")
        mw = it.get("middleware")
        if mw:
            lines.append("- middleware：")
            if mw.get("received_at_local"):
                delay = mw.get("delay_seconds")
                lines.append(
                    f"  - 收到時間：{mw['received_at_local']}"
                    + (f"（發生後 {delay:g} 秒）" if delay is not None else "")
                )
            if mw.get("source_zh"):
                lines.append(f"  - 收到管道：{mw['source_zh']}")
            if mw.get("message_status_zh"):
                lines.append(f"  - 訊息狀態：{mw['message_status_zh']}")
            if mw.get("stage_zh"):
                lines.append(f"  - 走到哪一關：{mw['stage_zh']}")
            occ = mw.get("occurrence") or {}
            if occ:
                span = occ.get("started_at_local") or "—"
                if occ.get("ended_at_local") and occ.get("ended_at_local") != span:
                    span += f" ～ {occ['ended_at_local']}"
                statuses = "、".join(
                    MESSAGE_STATUS_LABELS.get(x, x) for x in (occ.get("statuses") or [])
                )
                lines.append(
                    f"  - 一次發生：{span}；共 {occ.get('message_count') or '—'} 則訊息"
                    + (f"（{statuses}）" if statuses else "")
                )
            if mw.get("ui_url"):
                lines.append(
                    f"  - middleware 歷史事件頁：{mw['ui_url']}"
                    "（依事件類型篩選，頁面預設只顯示最近 24 小時）"
                )
        else:
            lines.append("- middleware：沒有這筆（或這次查不到）")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# --- 主流程 -------------------------------------------------------------------------


def _die(code: int, message: str) -> None:
    print(
        json.dumps(
            {"error": "usage" if code == 2 else "upstream", "message": message},
            ensure_ascii=False,
        )
    )
    sys.exit(code)


def _limit(args: argparse.Namespace) -> int:
    n = args.limit if args.limit is not None else DEFAULT_LIMIT
    if n < 1:
        _die(2, "--limit 至少 1")
    return min(n, HARD_LIMIT)


def _window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    if not args.start and not args.end:
        end = datetime.now(UTC)
        start = end - timedelta(hours=24)
        return start, end
    if not args.start or not args.end:
        _die(2, "--start 與 --end 要一起給")
    start, end = parse_time(args.start), parse_time(args.end)
    if end <= start:
        _die(2, "--end 要晚於 --start")
    return start, end


def assemble(
    client: Client,
    records: list[dict],
    *,
    flows_by_id: dict[int, dict] | None,
    with_plog: bool,
    save: bool,
    out_dir: Path,
) -> list[dict]:
    items: list[dict] = []
    for rec in records:
        rid = rec.get("event_record_id")
        flow = (
            (flows_by_id or {}).get(rid)
            if flows_by_id is not None
            else _flow_for_record(client, rec)
        )
        plog = (
            processed_log(client, rid) if (with_plog and isinstance(rid, int)) else None
        )
        item = describe(client, rec, flow, plog, rec.pop("_source", "observ"))
        item["files"] = save_images(client, item, out_dir) if save else []
        if not save:
            item.pop("_image_urls", None)
        items.append(item)
    return items


def _flow_for_record(client: Client, rec: dict) -> dict | None:
    ts = rec.get("timestamp")
    if not ts or not client.middleware_ok:
        return None
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    type_id = rec.get("event_type_id")
    rows = flow_rows(
        client,
        when - FLOW_NEIGHBOURHOOD,
        when + FLOW_NEIGHBOURHOOD,
        type_ids=[type_id] if isinstance(type_id, int) else None,
        max_pages=1,
        anchors_only=False,
    )
    for row in rows:
        if row.get("event_record_id") == rec.get("event_record_id"):
            return row
    return None


def _ids(records: list[dict]) -> set:
    return {r.get("event_record_id") for r in records}


def fallback_from_middleware(client: Client, missing: list[int]) -> list[dict]:
    """Observ 查不到的紀錄 id，改從 middleware 補。每一種下場都寫進 notes。"""
    out: list[dict] = []
    for rid in missing:
        if rid >= SYNTHETIC_ID_FLOOR:
            client.notes.append(
                f"{rid} 是 middleware 合成的「結束」訊息編號（13 位數），Observ 沒有這筆。"
                "請改用同一則訊息裡的 tracking_id 查。"
            )
            continue
        plog = processed_log(client, rid)
        if plog is None:
            client.notes.append(
                f"Observ 與 middleware 都查不到事件紀錄 {rid}，請確認編號。"
                if client.middleware_ok
                else f"Observ 查不到事件紀錄 {rid}，middleware 這次連不上、沒辦法補查。"
            )
            continue
        rec = record_from_middleware(plog)
        rec["_source"] = "middleware"
        out.append(rec)
        client.notes.append(
            f"Observ 查不到事件紀錄 {rid}（可能已被刪除或被 Observ 篩掉），"
            "內容改取自 middleware，所以不附 Observ 事件頁連結。"
        )
    return out


def fallback_from_flow(
    client: Client, rows: list[dict], records: list[dict]
) -> list[dict]:
    """列表查詢（name / type 帶篩選）裡，middleware 有、Observ 沒回的那幾筆。"""
    have = _ids(records)
    extra: list[dict] = []
    for row in rows:
        if row.get("event_record_id") in have:
            continue
        rec = record_from_middleware(row)
        rec["_source"] = "middleware"
        extra.append(rec)
    if extra:
        client.notes.append(
            f"有 {len(extra)} 筆 Observ 查不到（可能已被刪除或被 Observ 篩掉），"
            "內容改取自 middleware，那幾筆不附 Observ 事件頁連結。"
        )
    return extra


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="observ_lookup.py", add_help=True)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser, *, window: bool) -> None:
        sp.add_argument("--limit", type=int, default=None)
        sp.add_argument("--no-images", action="store_true")
        sp.add_argument("--out", default=".")
        if window:
            sp.add_argument("--start")
            sp.add_argument("--end")
            sp.add_argument("--vendor", choices=sorted(VENDOR_LABELS))
            sp.add_argument("--outcome", choices=sorted(OUTCOME_LABELS))

    s = sub.add_parser("record")
    s.add_argument("ids", nargs="+", type=int)
    common(s, window=False)
    s = sub.add_parser("tracking")
    s.add_argument("tracking_ids", nargs="+")
    common(s, window=False)
    s = sub.add_parser("type")
    s.add_argument("type_ids", nargs="+", type=int)
    common(s, window=True)
    s = sub.add_parser("name")
    s.add_argument("name")
    common(s, window=True)
    s = sub.add_parser("vlm-labels")
    s.add_argument("type_id", type=int)

    args = p.parse_args(argv)
    env = _Env()
    client = Client(env)
    out_dir = Path(getattr(args, "out", ".")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.cmd == "vlm-labels":
            print(
                json.dumps(
                    vlm_labels(client, args.type_id), ensure_ascii=False, indent=2
                )
            )
            return 0

        limit = _limit(args)
        save = not args.no_images
        query: dict[str, Any] = {"cmd": args.cmd}
        total: int

        if args.cmd in ("record", "tracking"):
            if args.cmd == "record":
                ids = list(dict.fromkeys(args.ids))
            else:
                ids = []
                for t in args.tracking_ids:
                    m = re.fullmatch(r"\s*(\d+)-(\d+)\s*", t)
                    if not m:
                        _die(
                            2,
                            f"tracking_id 長得不對：{t!r}，應該是 <task_id>-<event_record_id>",
                        )
                    ids.append(int(m.group(2)))
                query["tracking_ids"] = args.tracking_ids
            query["ids"] = ids
            total = len(ids)
            if len(ids) > limit:
                client.notes.append(f"一次最多 {limit} 筆，只查前 {limit} 筆。")
                ids = ids[:limit]
            records = observ_records_by_ids(client, ids)
            records += fallback_from_middleware(
                client, [i for i in ids if i not in _ids(records)]
            )
            records.sort(key=lambda r: ids.index(r["event_record_id"]))
            items = assemble(
                client,
                records,
                flows_by_id=None,
                with_plog=True,
                save=save,
                out_dir=out_dir,
            )

        elif args.cmd == "type":
            start, end = _window(args)
            query.update(
                type_ids=args.type_ids,
                start=iso_utc(start),
                end=iso_utc(end),
                vendor=args.vendor,
                outcome=args.outcome,
            )
            if args.vendor or args.outcome:
                rows = flow_rows(
                    client,
                    start,
                    end,
                    type_ids=args.type_ids,
                    vendor=args.vendor,
                    outcome=args.outcome,
                )
                rows.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
                total = len(rows)
                flows_by_id = {r["event_record_id"]: r for r in rows}
                records = observ_records_by_ids(
                    client, [r["event_record_id"] for r in rows[:limit]]
                )
                records += fallback_from_flow(client, rows[:limit], records)
            else:
                records, total = observ_records_by_type(
                    client, args.type_ids, start, end, limit
                )
                rows = flow_rows(client, start, end, type_ids=args.type_ids)
                flows_by_id = {r["event_record_id"]: r for r in rows}
            records.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
            items = assemble(
                client,
                records,
                flows_by_id=flows_by_id,
                with_plog=False,
                save=save,
                out_dir=out_dir,
            )

        else:  # name
            start, end = _window(args)
            query.update(
                name=args.name,
                start=iso_utc(start),
                end=iso_utc(end),
                vendor=args.vendor,
                outcome=args.outcome,
            )
            type_ids = translated_type_ids(client, args.name)
            rows = flow_rows(
                client,
                start,
                end,
                type_ids=type_ids or None,
                vendor=args.vendor,
                outcome=args.outcome,
            )
            needle = args.name.strip().lower()
            if not type_ids:
                rows = [
                    r
                    for r in rows
                    if needle in str(r.get("new_event_name") or "").lower()
                    or needle in str(r.get("event_name") or "").lower()
                ]
            rows.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
            total = len(rows)
            flows_by_id = {r["event_record_id"]: r for r in rows}
            records = observ_records_by_ids(
                client, [r["event_record_id"] for r in rows[:limit]]
            )
            records += fallback_from_flow(client, rows[:limit], records)
            records.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
            if not client.middleware_ok:
                client.notes.append(
                    "客戶端事件名稱要靠 middleware 反查，這次連不上，查不到。請改給事件紀錄 id 或 Observ 事件類型 id。"
                )
            items = assemble(
                client,
                records,
                flows_by_id=flows_by_id,
                with_plog=False,
                save=save,
                out_dir=out_dir,
            )

    except Expired:
        print(
            json.dumps(
                {"error": "observ_token_expired", "message": EXPIRED_MESSAGE},
                ensure_ascii=False,
            )
        )
        return 3
    except Upstream as exc:
        print(
            json.dumps(
                {
                    "error": "upstream",
                    "where": exc.where,
                    "status": exc.status,
                    "message": exc.detail,
                },
                ensure_ascii=False,
            )
        )
        return 4

    write_summary(out_dir / "summary.md", query, items, total, client.notes)
    files = ["summary.md"] + [f for it in items for f in it.get("files", [])]
    print(
        json.dumps(
            {
                "query": query,
                "total": total,
                "shown": len(items),
                "truncated": total > len(items),
                "notes": client.notes,
                "files": files,
                "records": items,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
