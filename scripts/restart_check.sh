#!/usr/bin/env bash
# Durability check: upload a clip, kill -9 the server mid-run, restart, and GET the analysis.
# Expected: the ID still exists, with status failed / interrupted_by_restart (or completed if it finished first).
set -euo pipefail

VIDEO="${1:?usage: scripts/restart_check.sh path/to/clip.mp4}"
PORT="${PORT:-9500}"
BASE="http://127.0.0.1:${PORT}"
LOG="var/restart_check.log"
mkdir -p var

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

echo "2) upload $VIDEO (fresh=true so it always runs)"
ID=$(curl -fs -X POST "$BASE/analyses?fresh=true" -F "file=@${VIDEO}" -F 'metadata={"external_id":"restart-check"}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "   id = $ID"
sleep 2
curl -fs "$BASE/analyses/$ID" | .venv/bin/python -c 'import json,sys; b=json.load(sys.stdin); print("   before kill:", b["status"], b["current_step"])'

echo "3) kill -9 $SERVER_PID"
kill -9 "$SERVER_PID"
wait "$SERVER_PID" 2>/dev/null || true

echo "4) restart"
start_server

echo "5) GET after restart"
curl -fs "$BASE/analyses/$ID" | .venv/bin/python -c '
import json, sys
b = json.load(sys.stdin)
print("   after restart:", b["status"], b["error"])
ok = b["status"] == "completed" or (b["error"] or {}).get("code") == "interrupted_by_restart"
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
'
kill "$SERVER_PID"
