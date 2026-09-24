"""`skills/observ-event-lookup/scripts/observ_lookup.py` 的行為，用 2026-09-24 對 production
抓回來、去識別化的回應形狀當 fixture。

它是 job 容器裡唯一碰得到委託者 Observ token 的東西，所以這裡釘的第一件事是
**token 不出現在任何輸出**；其餘是組裝（Observ 紀錄 ＋ middleware 轉發結果 ＋ VLM 中文對照）、
合成 id 過濾、上限、檔名、401 的固定訊息。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "job-claude/skills/observ-event-lookup/scripts/observ_lookup.py"
)
spec = importlib.util.spec_from_file_location("observ_lookup", SCRIPT)
ol = importlib.util.module_from_spec(spec)
sys.modules["observ_lookup"] = ol
spec.loader.exec_module(ol)

# 假 JWT：header {"alg":"RS256"}、payload {"exp":9999999999}、簽章是字串。它長得像 JWT
# 是刻意的 —— 要驗的正是「長得像 token 的東西不會出現在輸出裡」。
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.fake-signature-do-not-leak"  # gitleaks:allow
SID = "00000000-0000-0000-0000-000000000000"
OBSERV = "https://observ.example.com/observ"
MW = "https://middleware.example.com:8443"

RECORD = {
    "event_record_id": 1111954,
    "task_id": 6053,
    "event_type_id": 10650,
    "event_name": "B-80-道路鋪面坑洞辨識-BUS-v17.1-flow-L-M",
    "event_description": "",
    "event_record_status": "TBC",
    "timestamp": "2026-07-24T09:47:03.516859Z",
    "video_time": None,
    "image_url": f"{OBSERV}/media-provider/file/{SID}/image/vlm/6053/entry/2026/07/24/abc.jpg",
    "image_urls": [
        f"{OBSERV}/media-provider/file/{SID}/image/vlm/6053/entry/2026/07/24/abc.jpg"
    ],
    "video_url": None,
    "location_id": 13,
    "camera_id": 5180,
    "video_id": None,
    "timezone": "Asia/Taipei",
    "vlms": [
        {
            "vlm_template_id": 10239,
            "vlm_template_name": "B-80-道路鋪面坑洞辨識-v17.1",
            "results": [
                {"key": "what is the weather condition", "val": ["B. Cloudy"]},
                {"key": "is the pothole on the drivable lane", "val": True},
            ],
        }
    ],
    "coordinates": {"latitude": 22.85359, "longitude": 120.26131},
    "note": "",
    "updated_timestamp": "2026-07-24T09:47:03.516859Z",
}

FLOW_ANCHOR = {
    "id": 1,
    "event_record_id": 1111954,
    "tracking_id": "6053-1111954",
    "task_id": 6053,
    "event_type_id": 10650,
    "new_event_id": 6080,
    "new_event_name": "道路鋪面坑洞辨識",
    "cctv_id": "BT017762   F2",
    "image_url": RECORD["image_url"],
    "video_url": None,
    "vlms": RECORD["vlms"],
    "stage": "configured",
    "outcome": "gate_blocked",
    "gate": {
        "is_gated": True,
        "passed": False,
        "verification_source": "gemma4",
        "failure_code": None,
        "answer": '[ "false" ]',
        "ensemble_decision": None,
    },
    "whitelisted_for": {"iisi": False, "cht": True},
    "delivered_to": {"iisi": False, "cht": False},
    "annotation": None,
    "timestamp": RECORD["timestamp"],
}
# 「結束」訊息：合成的 event_record_id（epoch 毫秒）。Observ 沒有這筆。
FLOW_END = {
    **FLOW_ANCHOR,
    "id": 2,
    "event_record_id": 1753350000000,
    "status": "end",
    "stage": "configured",
}
FLOW_OCC = {
    "tracking_id": "6053-1111954",
    "anchor": FLOW_ANCHOR,
    "members": [FLOW_ANCHOR, FLOW_END],
}

PLOG = {
    "id": 1,
    "tracking_id": "6053-1111954",
    "event_record_id": 1111954,
    "status": "start",
    "source": "observ_socket",
    "event_record_status": "TBC",
    "gemma_answer": '[ "false" ]',
    "ensemble_decision": None,
    "verification_source": "gemma4",
    "cctv_id": "BT017762   F2",
}

LABELS = {
    "event_type_id": 10650,
    "keys": {
        "what is the weather condition": {
            "question": "天氣狀態",
            "answers": {"A. Sunny": "晴天", "B. Cloudy": "陰天"},
        },
        "is the pothole on the drivable lane": {
            "question": "坑洞是否在車道上",
            "answers": {"True": "是", "False": "否"},
        },
    },
}

JPEG = b"\xff\xd8\xff" + b"0" * 100


class FakeClient(ol.Client):
    """把 HTTP 換成 fixture。記下每個請求，讓測試能斷言 token 只出現在 header / query。"""

    def __init__(self, env, *, observ=None, middleware=None):
        super().__init__(env)
        self.calls: list[tuple[str, str]] = []
        self._observ = observ or {}
        self._mw = middleware or {}

    def _get_json(self, url, headers, *, where, opener=None):
        self.calls.append((where, url))
        table = self._observ if where == "Observ" else self._mw
        for prefix, handler in table.items():
            if prefix in url:
                result = handler(url) if callable(handler) else handler
                if isinstance(result, ol.Upstream | ol.Expired):
                    raise result
                return result
        raise AssertionError(f"unexpected {where} call: {url}")

    def download(self, url):
        self.calls.append(("download", url))
        return JPEG


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("OBSERV_TOKEN", TOKEN)
    monkeypatch.setenv("OBSERV_BASE_URL", OBSERV)
    monkeypatch.setenv("OBSERV_SERVICE_ID", SID)
    monkeypatch.setenv("MIDDLEWARE_BASE_URL", MW)
    return ol._Env()


def _page(results, total=None):
    return {
        "page": 1,
        "size": 50,
        "total": total if total is not None else len(results),
        "total_pages": 1,
        "results": results,
    }


def _client(env, **overrides) -> FakeClient:
    observ = {"/v2/events/record": _page([RECORD])}
    mw = {
        "/pm/event-flow": _page([FLOW_OCC]),
        "/v2/processed-event-logs": _page([PLOG]),
        "/pm/annotations/vlm-labels/10650": LABELS,
        "/v2/event-translate": ol.Upstream("middleware", 404, "Not Found"),
    }
    observ.update(overrides.pop("observ", {}))
    mw.update(overrides.pop("middleware", {}))
    return FakeClient(env, observ=observ, middleware=mw)


def _run(monkeypatch, client, argv, tmp_path, capsys) -> tuple[int, dict, str]:
    monkeypatch.setattr(ol, "Client", lambda env: client)
    code = ol.main([*argv, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else {}), out


# --- token 不外流 --------------------------------------------------------------


def test_token_appears_only_in_headers_or_download_query(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env)
    code, data, raw = _run(monkeypatch, client, ["record", "1111954"], tmp_path, capsys)
    assert code == 0
    assert TOKEN not in raw
    assert TOKEN not in (tmp_path / "summary.md").read_text()
    for name in data["files"]:
        assert TOKEN not in name
    # 查詢的網址裡沒有 token（它在 header）；只有 download() 會把它放進 query，而那條不印。
    for where, url in client.calls:
        if where != "download":
            assert TOKEN not in url


def test_download_puts_the_token_in_the_query_not_a_header(env) -> None:
    """media-provider 只認 query 的 auth_token（2026-09-24 實測 Bearer 回 401）。"""
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return JPEG

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return Resp()

    import urllib.request

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        assert ol.Client(env).download(RECORD["image_url"]) == JPEG
    finally:
        urllib.request.urlopen = orig
    assert "auth_token=" in seen["url"] and f"service_id={SID}" in seen["url"]


# --- 組裝 ---------------------------------------------------------------------


def test_record_is_enriched_from_both_sides(env, monkeypatch, tmp_path, capsys) -> None:
    client = _client(env)
    code, data, _ = _run(monkeypatch, client, ["record", "1111954"], tmp_path, capsys)
    assert code == 0
    (item,) = data["records"]
    assert item["timestamp_local"] == "2026-07-24 17:47:03 CST"
    assert item["review_status_zh"] == "待確認"
    assert item["customer_event_name"] == "道路鋪面坑洞辨識"
    assert item["tracking_id"] == "6053-1111954"
    assert item["forwarding"]["outcome_zh"] == "被模型擋下"
    assert item["forwarding"]["delivered_to"] == []
    assert item["forwarding"]["whitelisted_for"] == ["中華"]
    assert item["verification"]["passed_zh"] == "沒通過"
    assert item["verification"]["source"] == "gemma4"
    assert (
        item["observ_url"]
        == "https://observ.example.com/observ/history?eventLogModalId=1111954"
    )
    # VLM 回答有中文對照，包括 boolean 那種。
    zh = {v["question_zh"]: v["answer_zh"] for v in item["vlm"]}
    assert zh == {"天氣狀態": ["陰天"], "坑洞是否在車道上": ["是"]}
    # 截圖檔名對齊 middleware UI 的慣例：event_<id>_<台北時間>.jpg
    assert item["files"] == ["event_1111954_20260724-174703.jpg"]
    assert (tmp_path / "event_1111954_20260724-174703.jpg").read_bytes() == JPEG
    assert "summary.md" in data["files"]
    summary = (tmp_path / "summary.md").read_text()
    assert "被模型擋下" in summary and "中華" in summary and "陰天" in summary


def test_tracking_id_resolves_to_its_start_record(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env)
    code, data, _ = _run(
        monkeypatch,
        client,
        ["tracking", "6053-1111954", "--no-images"],
        tmp_path,
        capsys,
    )
    assert code == 0
    assert data["records"][0]["event_record_id"] == 1111954
    assert data["records"][0]["files"] == []


def test_bad_tracking_id_is_a_usage_error(env, monkeypatch, tmp_path, capsys) -> None:
    client = _client(env)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, client, ["tracking", "abc"], tmp_path, capsys)
    assert exc.value.code == 2


def test_missing_record_is_explained_not_silent(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env, observ={"/v2/events/record": _page([])})
    code, data, _ = _run(
        monkeypatch, client, ["record", "1753350000000"], tmp_path, capsys
    )
    assert code == 0 and data["records"] == []
    assert any("tracking_id" in n for n in data["notes"])


def test_middleware_down_degrades_to_observ_only(
    env, monkeypatch, tmp_path, capsys
) -> None:
    down = ol.Upstream("middleware", None, "connection refused")
    client = _client(
        env,
        middleware={
            "/pm/event-flow": down,
            "/v2/processed-event-logs": down,
            "/pm/annotations/vlm-labels/10650": down,
        },
    )
    code, data, _ = _run(
        monkeypatch, client, ["record", "1111954", "--no-images"], tmp_path, capsys
    )
    assert code == 0
    (item,) = data["records"]
    assert item["forwarding"] is None
    assert item["event_name"].startswith("B-80")
    assert any("middleware 連不上" in n for n in data["notes"])


# --- 列表：anchor only、合成 id、上限 ----------------------------------------------


def test_list_queries_use_anchors_and_skip_synthetic_end_ids(env) -> None:
    client = _client(env)
    start, end = (
        ol.parse_time("2026-07-24T00:00:00+08:00"),
        ol.parse_time("2026-07-25T00:00:00+08:00"),
    )
    rows = ol.flow_rows(client, start, end, type_ids=[10650])
    assert [r["event_record_id"] for r in rows] == [1111954]
    with_members = ol.flow_rows(
        client, start, end, type_ids=[10650], anchors_only=False
    )
    # 就算連 members 一起看，合成的 13 位數 id 也不會被拿去 Observ 查。
    assert [r["event_record_id"] for r in with_members] == [1111954]


def test_type_query_is_capped_and_reports_the_total(
    env, monkeypatch, tmp_path, capsys
) -> None:
    many = [{**RECORD, "event_record_id": 1111954 + i} for i in range(60)]

    def observ_page(url):
        return _page(many[:50], total=611)

    client = _client(env, observ={"/v2/events/record": observ_page})
    code, data, _ = _run(
        monkeypatch,
        client,
        [
            "type",
            "10650",
            "--start",
            "2026-07-24T00:00:00+08:00",
            "--end",
            "2026-07-25T00:00:00+08:00",
            "--limit",
            "99",
            "--no-images",
        ],
        tmp_path,
        capsys,
    )
    assert code == 0
    assert data["total"] == 611
    assert data["shown"] == ol.HARD_LIMIT  # --limit 99 被夾到 40
    assert data["truncated"] is True
    assert "已達上限" in (tmp_path / "summary.md").read_text()


def test_name_falls_back_to_scanning_event_flow_when_translate_is_missing(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env)
    code, data, _ = _run(
        monkeypatch,
        client,
        [
            "name",
            "坑洞",
            "--start",
            "2026-07-24T00:00:00+08:00",
            "--end",
            "2026-07-25T00:00:00+08:00",
            "--vendor",
            "cht",
            "--no-images",
        ],
        tmp_path,
        capsys,
    )
    assert code == 0
    assert [r["event_record_id"] for r in data["records"]] == [1111954]
    flow_calls = [u for w, u in client.calls if "/pm/event-flow" in u]
    assert flow_calls and "vendors=cht" in flow_calls[0]


def test_name_that_matches_nothing_is_empty_not_an_error(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env)
    code, data, _ = _run(
        monkeypatch, client, ["name", "淹水", "--no-images"], tmp_path, capsys
    )
    assert code == 0 and data["records"] == [] and data["total"] == 0


def test_window_default_is_24h_and_end_must_follow_start(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env)
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            client,
            [
                "type",
                "10650",
                "--start",
                "2026-07-25T00:00:00+08:00",
                "--end",
                "2026-07-24T00:00:00+08:00",
            ],
            tmp_path,
            capsys,
        )
    assert exc.value.code == 2


# --- 401 -----------------------------------------------------------------------


def test_expired_token_is_exit_3_with_the_fixed_message(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(env, observ={"/v2/events/record": ol.Expired()})
    code, data, _ = _run(monkeypatch, client, ["record", "1111954"], tmp_path, capsys)
    assert code == 3
    assert data == {"error": "observ_token_expired", "message": ol.EXPIRED_MESSAGE}


def test_missing_env_is_exit_2_and_names_the_variables(monkeypatch, capsys) -> None:
    for name in (
        "OBSERV_TOKEN",
        "OBSERV_BASE_URL",
        "OBSERV_SERVICE_ID",
        "MIDDLEWARE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as exc:
        ol._Env()
    assert exc.value.code == 2
    assert "OBSERV_TOKEN" in capsys.readouterr().out


# --- 時間 ----------------------------------------------------------------------


def test_naive_times_are_taipei(env) -> None:
    assert ol.iso_utc(ol.parse_time("2026-07-24T08:00:00")) == "2026-07-24T00:00:00Z"
    assert ol.iso_utc(ol.parse_time("2026-07-24T00:00:00Z")) == "2026-07-24T00:00:00Z"


# --- Observ 查不到、middleware 有（2026-09-24，1111955 的實例）---------------------

# 1111955 的真實形狀：Observ 的查詢 API 回 0 筆，middleware 的 processed-event-logs 有完整一列。
PLOG_1111955 = {
    "id": 2,
    "event_record_id": 1111955,
    "tracking_id": "8053-1111955",
    "status": "start",
    "source": "observ_socket",
    "task_id": 8053,
    "event_type_id": 10717,
    "event_name": "B-13-單向或雙向交通阻斷-CCTV-v17.1-flow-time60",
    "event_description": "",
    "event_record_status": "TBC",
    "timestamp": "2026-07-24T09:47:11.571803Z",
    # middleware 的 created_at 沒帶時區，是它主機的台北時間。
    "created_at": "2026-07-24T17:47:14.215196",
    "image_url": f"{OBSERV}/media-provider/file/{SID}/image/vlm/8053/entry/x.jpg",
    "video_url": None,
    "location_id": 81,
    "camera_id": 405,
    "timezone": "Asia/Taipei",
    "vlms": [],
    "detected_objects": [],
    "coordinates_lat": 22.0,
    "coordinates_lon": 120.0,
    "note": "",
    "gemma_answer": '[ "True" ]',
    "ensemble_decision": None,
    "verification_source": "gemma4",
    "cctv_id": "CCTV-405",
}


def _plog_by_id(url):
    rid = int(url.split("event_record_id=")[1].split("&")[0])
    return _page([PLOG_1111955] if rid == 1111955 else [])


def test_record_missing_in_observ_is_filled_from_middleware(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(
        env,
        observ={"/v2/events/record": _page([])},
        middleware={
            "/v2/processed-event-logs": _plog_by_id,
            "/pm/event-flow": _page([]),
            "/pm/annotations/vlm-labels/10717": {"keys": {}},
        },
    )
    code, data, _ = _run(monkeypatch, client, ["record", "1111955"], tmp_path, capsys)
    assert code == 0
    (item,) = data["records"]
    assert item["data_source"] == "middleware"
    assert item["event_name"].startswith("B-13-單向或雙向交通阻斷")
    assert item["timestamp_local"] == "2026-07-24 17:47:11 CST"
    assert item["coordinates"] == {"latitude": 22.0, "longitude": 120.0}
    # Observ 查不到的紀錄不給 Observ 連結：那一頁開起來是空的。
    assert item["observ_url"] is None
    assert item["verification"]["answer"] == '[ "True" ]'
    # 截圖照樣抓（middleware 記的是 Observ 的 media-provider 網址）。
    assert item["files"] == ["event_1111955_20260724-174711.jpg"]
    note = " ".join(data["notes"])
    assert "Observ 查不到事件紀錄 1111955" in note and "middleware" in note
    # 7 位數的編號不該被猜成合成的「結束」訊息編號。
    assert "合成" not in note
    summary = (tmp_path / "summary.md").read_text()
    assert "Observ 查不到這筆" in summary and "**Observ 事件頁**" not in summary
    assert "eventLogModalId" not in summary


def test_missing_in_both_says_so_without_guessing(
    env, monkeypatch, tmp_path, capsys
) -> None:
    client = _client(
        env,
        observ={"/v2/events/record": _page([])},
        middleware={"/v2/processed-event-logs": _page([])},
    )
    code, data, _ = _run(monkeypatch, client, ["record", "1234567"], tmp_path, capsys)
    assert code == 0 and data["records"] == []
    (note,) = data["notes"]
    assert "Observ 與 middleware 都查不到事件紀錄 1234567" in note
    assert "合成" not in note and "tracking_id" not in note


def test_processed_log_ignores_rows_for_another_record(env) -> None:
    """上游篩選沒生效、回了別筆時，不能把別筆當成這筆。"""
    client = _client(env)  # processed-event-logs 永遠回 1111954 那列
    assert ol.processed_log(client, 1111955) is None
    assert ol.processed_log(client, 1111954)["tracking_id"] == "6053-1111954"


# --- middleware 細節給 PM ---------------------------------------------------------


def test_found_in_observ_gets_the_link_and_middleware_details(
    env, monkeypatch, tmp_path, capsys
) -> None:
    occ = {
        **FLOW_OCC,
        "started_at": "2026-07-24T09:47:03.516859Z",
        "ended_at": "2026-07-24T09:48:03.516859Z",
        "duration_seconds": 60,
        "statuses": ["start", "end"],
        "message_count": 2,
    }
    plog = {**PLOG, "created_at": "2026-07-24T17:47:06.131634"}
    client = _client(
        env,
        middleware={
            "/pm/event-flow": _page([occ]),
            "/v2/processed-event-logs": _page([plog]),
        },
    )
    code, data, _ = _run(
        monkeypatch, client, ["record", "1111954", "--no-images"], tmp_path, capsys
    )
    assert code == 0
    (item,) = data["records"]
    assert item["data_source"] == "observ"
    assert item["observ_url"].endswith("eventLogModalId=1111954")
    mw = item["middleware"]
    assert mw["received_at_local"] == "2026-07-24 17:47:06 CST"
    assert mw["delay_seconds"] == 2.6
    assert mw["source_zh"] == "Observ WS 即時推送"
    assert mw["message_status_zh"] == "開始"
    assert mw["stage_zh"] == "已設定白名單"
    assert mw["occurrence"]["tracking_id"] == "6053-1111954"
    assert mw["occurrence"]["message_count"] == 2
    assert mw["occurrence"]["ended_at_local"] == "2026-07-24 17:48:03 CST"
    assert mw["ui_url"] == f"{MW}/middleware-ui/#/event-flow?event_type=10650"
    summary = (tmp_path / "summary.md").read_text()
    assert "**Observ 事件頁**" in summary
    assert "收到時間：2026-07-24 17:47:06 CST（發生後 2.6 秒）" in summary
    assert "共 2 則訊息（開始、結束）" in summary
    assert "middleware 歷史事件頁" in summary


def test_name_listing_keeps_rows_observ_does_not_return(
    env, monkeypatch, tmp_path, capsys
) -> None:
    """客戶端名稱從 middleware 起查；Observ 沒回的那筆不能被默默丟掉。"""
    client = _client(env, observ={"/v2/events/record": _page([])})
    code, data, _ = _run(
        monkeypatch,
        client,
        [
            "name",
            "坑洞",
            "--start",
            "2026-07-24T00:00:00+08:00",
            "--end",
            "2026-07-25T00:00:00+08:00",
            "--no-images",
        ],
        tmp_path,
        capsys,
    )
    assert code == 0
    (item,) = data["records"]
    assert item["event_record_id"] == 1111954
    assert item["data_source"] == "middleware" and item["observ_url"] is None
    assert item["forwarding"]["outcome_zh"] == "被模型擋下"
    assert any("Observ 查不到" in n for n in data["notes"])
