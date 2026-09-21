#!/usr/bin/env bash
# 端到端打過一遍 Hub 的協定（SPEC.md §9）。不需要 worker，用 curl 扮演它。
set -euo pipefail
H="${HUB_URL:-http://127.0.0.1:8787}"
j() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }

echo "1. 註冊 worker"
TOKEN=$(curl -sS -X POST "$H/api/worker/register" -H 'content-type: application/json' \
  -d '{"name":"vivian-laptop","available_models":["sonnet","haiku"],"claude_code_version":"2.1.278"}' | j "['token']")
echo "   token 取得（${#TOKEN} 字元）"

echo "2. 借用者提交 job"
JOB=$(curl -sS -X POST "$H/api/jobs" -H 'content-type: application/json' \
  -d '{"borrower_label":"BD Kevin","prompt":"幫我看一下這份需求","model":"sonnet"}' | j "['id']")
echo "   job_id=$JOB"

echo "3. worker long-poll 領單"
CLAIMED=$(curl -sS "$H/api/worker/poll" -H "X-Worker-Token: $TOKEN" | j "['job_id']")
[ "$CLAIMED" = "$JOB" ] && echo "   ✅ 領到同一筆" || { echo "   ❌ 領到 $CLAIMED"; exit 1; }

echo "4. worker 推送串流事件"
curl -sS -X POST "$H/api/worker/jobs/$JOB/events" -H "X-Worker-Token: $TOKEN" \
  -H 'content-type: application/json' -d '{
    "from_seq": 1,
    "events": [
      {"type":"system","subtype":"init"},
      {"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{"five_hour":{"utilization":0.34},"seven_day":{"utilization":0.12}}}},
      {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}
    ]}' | j "" 

echo "5. 回報結果"
curl -sS -X POST "$H/api/worker/jobs/$JOB/result" -H "X-Worker-Token: $TOKEN" \
  -H 'content-type: application/json' -d '{
    "status":"succeeded","result_text":"看完了，建議分三階段","total_cost_usd":"2.30",
    "lender_cli_version":"2.1.278",
    "model_usage":{"claude-sonnet-5":{"inputTokens":908,"outputTokens":54,
      "cacheReadInputTokens":22333,"cacheCreationInputTokens":7991,"thinkingTokens":34,
      "costUSD":2.30,"canonicalModel":"claude-sonnet-5","costBasis":"list"}}}' | j ""

echo "6. 借用者看 job 詳情"
curl -sS "$H/api/jobs/$JOB" | python3 -c "
import sys,json;d=json.load(sys.stdin)
print('   status     =',d['status'])
print('   cost       =',d['total_cost_usd'])
print('   debt_label =',d['debt_label'])
print('   版本 借/出 =',d['borrower_cli_version'],'/',d['lender_cli_version'])"

echo "7. 事件重播（poll fallback）"
curl -sS "$H/api/jobs/$JOB/events?from_seq=0" | python3 -c "
import sys,json;d=json.load(sys.stdin)
print('   事件數:',len(d))
for e in d: print('   seq',e['seq'],e['payload'].get('type'))"

echo "8. SSE 重播"
curl -sS --max-time 5 -N "$H/api/jobs/$JOB/stream?from_seq=0" 2>/dev/null | head -6 | sed 's/^/   /'
