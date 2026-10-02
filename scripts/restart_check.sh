#!/usr/bin/env bash
# Durability check: upload a clip, kill -9 the server mid-run, restart, and GET the analysis.
# Expected: the ID still exists, with status failed / interrupted_by_restart (or completed if it finished first),
# and the most recent completed analysis, if there is one, comes back byte for byte as it was before the kill.
# With Basic auth on (BASIC_AUTH_USER/PASSWORD in .env), pass the same pair: CLIP_API_AUTH=user:password.
set -euo pipefail

VIDEO="${1:?usage: scripts/restart_check.sh path/to/clip.mp4}"
PORT="${PORT:-9500}"
BASE="http://127.0.0.1:${PORT}"
LOG="var/restart_check.log"
mkdir -p var
AUTH=()
[[ -n "${CLIP_API_AUTH:-}" ]] && AUTH=(-u "$CLIP_API_AUTH")
SERVER_PID=""
# Never leave a server behind, whether the check passes or stops halfway.
trap '[[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null; true' EXIT

start_server() {
  # Run the venv's python directly so kill -9 hits the server itself, not a wrapper.
  .venv/bin/python -m uvicorn app.main:app --port "$PORT" >>"$LOG" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 60); do
    curl -fs "$BASE/health" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "server did not start; see $LOG" >&2
  exit 1
}

echo "1) start server"
start_server
if [[ "$(curl -s -o /dev/null -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} "$BASE/analyses?limit=1")" == 401 ]]; then
  echo "401: Basic auth is on. Run with CLIP_API_AUTH=user:password (BASIC_AUTH_USER/PASSWORD from .env)." >&2
  exit 1
fi

DONE_FILE="var/restart_check.completed.json"
rm -f "$DONE_FILE"
curl -fsS ${AUTH[@]+"${AUTH[@]}"} "$BASE/analyses?limit=200" | .venv/bin/python -c '
import json, sys
done = [a["id"] for a in json.load(sys.stdin)["items"] if a["status"] == "completed"]
print(done[0] if done else "")
' >"$DONE_FILE.id"
DONE_ID=$(cat "$DONE_FILE.id")
if [[ -n "$DONE_ID" ]]; then
  curl -fsS ${AUTH[@]+"${AUTH[@]}"} "$BASE/analyses/$DONE_ID" >"$DONE_FILE"
  echo "   completed analysis to recheck: $DONE_ID"
else
  echo "   no completed analysis yet: only the interrupted one is checked"
fi

echo "2) upload $VIDEO (fresh=true so it always runs)"
ID=$(curl -fsS ${AUTH[@]+"${AUTH[@]}"} -X POST "$BASE/analyses?fresh=true" -F "file=@${VIDEO}" -F 'metadata={"external_id":"restart-check"}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "   id = $ID"
sleep 2
curl -fsS ${AUTH[@]+"${AUTH[@]}"} "$BASE/analyses/$ID" | .venv/bin/python -c 'import json,sys; b=json.load(sys.stdin); print("   before kill:", b["status"], b["current_step"])'

echo "3) kill -9 $SERVER_PID"
kill -9 "$SERVER_PID"
wait "$SERVER_PID" 2>/dev/null || true

echo "4) restart"
start_server

echo "5) GET after restart"
curl -fsS ${AUTH[@]+"${AUTH[@]}"} "$BASE/analyses/$ID" | .venv/bin/python -c '
import json, sys
b = json.load(sys.stdin)
print("   after restart:", b["status"], b["error"])
ok = b["status"] == "completed" or (b["error"] or {}).get("code") == "interrupted_by_restart"
if not ok:
    print("FAIL")
sys.exit(0 if ok else 1)
'
if [[ -f "$DONE_FILE" ]]; then
  curl -fsS ${AUTH[@]+"${AUTH[@]}"} "$BASE/analyses/$DONE_ID" | .venv/bin/python -c '
import json, sys
after, before = json.load(sys.stdin), json.load(open(sys.argv[1]))
same = after == before
print("   completed", before["id"], "after restart:", "identical" if same else "CHANGED")
if not same:
    print("FAIL")
sys.exit(0 if same else 1)
' "$DONE_FILE"
fi
echo PASS
