#!/usr/bin/env bash
#
# Brings the local SonarQube up and leaves a usable analysis token on disk.
#
# Idempotent on purpose: a review harness that only works on a clean machine is
# a harness nobody runs twice. Re-running this against a server that is already
# up, already has its password changed and already has a valid token is a no-op.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="${SONAR_URL:-http://localhost:9002}"
PASSWORD="${SONAR_PASSWORD:-ScoringAudit2026!}"
TOKEN_FILE="${HERE}/.sonar-token"
BOOT_TIMEOUT="${SONAR_BOOT_TIMEOUT:-300}"

say() { printf '\033[36m[sonar]\033[0m %s\n' "$1"; }
die() { printf '\033[31m[sonar]\033[0m %s\n' "$1" >&2; exit 1; }

command -v docker >/dev/null || die "docker is not on PATH"

say "starting the container"
docker compose -f "${HERE}/docker-compose.yml" up -d

say "waiting for the server to report UP (this takes a minute on a cold start)"
deadline=$(( SECONDS + BOOT_TIMEOUT ))
until curl -fsS "${URL}/api/system/status" 2>/dev/null | grep -q '"status":"UP"'; do
    [ "$SECONDS" -lt "$deadline" ] || die "gave up after ${BOOT_TIMEOUT}s; try: docker compose -f ${HERE}/docker-compose.yml logs"
    sleep 5
done
say "server is up at ${URL}"

# SonarQube ships with admin/admin and refuses to do anything useful until that
# changes. The three outcomes are genuinely different and must not be collapsed:
# 2xx means we just changed it, 401 means a previous run already did, and
# anything else is a real failure — notably 400, which is how the server rejects
# a password that does not meet its complexity rules.
body=$(mktemp)
trap 'rm -f "$body"' EXIT
status=$(curl -s -o "$body" -w '%{http_code}' -u "admin:admin" \
    -X POST "${URL}/api/users/change_password" \
    -d "login=admin&previousPassword=admin&password=${PASSWORD}")

case "$status" in
    2*)  say "admin password set" ;;
    401) say "default credentials rejected, so the password is already set" ;;
    *)   die "could not set the admin password (HTTP ${status}): $(cat "$body")" ;;
esac

curl -fsS -u "admin:${PASSWORD}" "${URL}/api/authentication/validate" 2>/dev/null \
    | grep -q '"valid":true' \
    || die "cannot authenticate as admin; set SONAR_PASSWORD to the one this server has, or run 'make sonar-clean' to start over"

# Reuse the stored token when it still authenticates. Tokens cannot be read back
# out of SonarQube, so a lost file means minting a new one, not recovering it.
if [ -f "$TOKEN_FILE" ] && curl -fsS -u "$(cat "$TOKEN_FILE"):" \
        "${URL}/api/authentication/validate" 2>/dev/null | grep -q '"valid":true'; then
    say "reusing the existing analysis token"
    exit 0
fi

say "minting an analysis token"
token=$(curl -fsS -u "admin:${PASSWORD}" -X POST "${URL}/api/user_tokens/generate" \
    -d "name=scoring-harness-$(date +%s)" \
    | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')

[ -n "$token" ] || die "could not mint a token; is the admin password '${PASSWORD}'?"

printf '%s' "$token" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
say "token written to quality/.sonar-token (gitignored)"
