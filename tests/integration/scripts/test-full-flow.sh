#!/usr/bin/env bash
# Automated integration test for the full Buncker flow.
#
# Runs from the host machine inside tests/integration/.
# Prerequisites:
#   make build-deb
#   cd tests/integration
#   docker compose up -d --build
#
# Usage:
#   bash scripts/test-full-flow.sh
#
# Tests both modes:
#   Phase 1: USB flow (no API auth) - traditional air-gapped cycle
#   Phase 2: LAN client flow (API auth) - curl-based operations with tokens

set -euo pipefail

# Prevent MSYS/Git Bash from mangling Unix-style paths in docker commands
export MSYS_NO_PATHCONV=1

COMPOSE="docker compose"
PASS=0
FAIL=0
TOTAL=0

# Resolve script dir relative to integration/ (where docker compose runs)
SCRIPT_DIR="scripts"

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

check() {
    TOTAL=$((TOTAL + 1))
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        green "  [PASS] $desc"
        PASS=$((PASS + 1))
    else
        red "  [FAIL] $desc"
        FAIL=$((FAIL + 1))
    fi
}

check_output() {
    TOTAL=$((TOTAL + 1))
    local desc="$1"
    local expected="$2"
    shift 2
    local output
    output=$("$@" 2>&1) || true
    if echo "$output" | grep -q "$expected"; then
        green "  [PASS] $desc"
        PASS=$((PASS + 1))
    else
        red "  [FAIL] $desc (expected '$expected' in output)"
        echo "    Got: $output"
        FAIL=$((FAIL + 1))
    fi
}

exec_offline() { $COMPOSE exec -T buncker-offline "$@"; }
exec_online()  { $COMPOSE exec -T online "$@"; }
exec_client()  { $COMPOSE exec -T client "$@"; }

# ---------------------------------------------------------------
# Setup: copy helper scripts into containers
# ---------------------------------------------------------------

bold "=== Copying helper scripts into containers ==="
$COMPOSE cp "${SCRIPT_DIR}/sync-salt.py" online:/tmp/sync-salt.py
$COMPOSE cp "${SCRIPT_DIR}/fetch-manifest.py" online:/tmp/fetch-manifest.py
$COMPOSE cp "${SCRIPT_DIR}/inject-manifest.py" buncker-offline:/tmp/inject-manifest.py

# ---------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------

bold "=== Pre-flight checks ==="

check "buncker-offline container is running" \
    exec_offline echo ok

check "online container is running" \
    exec_online echo ok

check "client container is running" \
    exec_client echo ok

check "buncker-offline has no internet" \
    bash -c "! $COMPOSE exec -T buncker-offline curl -s --connect-timeout 2 https://google.com 2>/dev/null"

check "client has no internet" \
    bash -c "! $COMPOSE exec -T client curl -s --connect-timeout 2 https://google.com 2>/dev/null"

# ---------------------------------------------------------------
# Phase 1: USB Flow (no API auth)
# ---------------------------------------------------------------

bold ""
bold "=== Phase 1: USB Flow (no API auth) ==="

# Setup buncker
bold "  -- Step 1: Setup buncker --"
SETUP_OUTPUT=$(exec_offline buncker setup 2>&1) || true
echo "$SETUP_OUTPUT"

# Extract mnemonic from env file (supports encrypted and cleartext formats)
MNEMONIC=$(exec_offline python3 -c "
import sys, re
sys.path.insert(0, '/usr/lib/buncker')
env = open('/etc/buncker/env').read().strip()
m = re.match(r'BUNCKER_MNEMONIC_ENC=(.*)', env)
if m:
    from shared.crypto import decrypt_env_value
    print(decrypt_env_value(m.group(1)))
else:
    m = re.match(r'BUNCKER_MNEMONIC=(.*)', env)
    if m:
        print(m.group(1))
")
echo "  Mnemonic: $MNEMONIC"

check "buncker setup created config" \
    exec_offline test -f /etc/buncker/config.json

check "buncker setup created env file" \
    exec_offline test -f /etc/buncker/env

# Start daemon in background (no systemd in Docker)
# Use nohup + redirect to keep it alive after exec returns
exec_offline bash -c "nohup bash -c 'BUNCKER_MNEMONIC=\"$MNEMONIC\" buncker serve' > /tmp/buncker.log 2>&1 &"
sleep 3

check "buncker daemon is listening" \
    exec_offline curl -sf http://127.0.0.1:5000/v2/

# Get salt for pairing
SALT=$(exec_offline python3 -c "import json; print(json.load(open('/etc/buncker/config.json'))['crypto']['salt'])")

# Pair online side (use split_mnemonic to extract 12-word secret + salt from 16-word mnemonic)
bold "  -- Step 2: Pair online side --"
exec_online python3 -c "
import base64, json, sys, pathlib
sys.path.insert(0, '/usr/lib/buncker-fetch')
from shared.crypto import derive_keys, encrypt, split_mnemonic
mnemonic_12, salt = split_mnemonic('$MNEMONIC')
aes_key, _ = derive_keys(mnemonic_12, salt)
marker = b'buncker-pair-check'
check = base64.b64encode(encrypt(marker, aes_key)).decode()
config = {'salt': base64.b64encode(salt).decode(), 'derived_key_check': check, 'registries': {}}
pathlib.Path('/root/.buncker').mkdir(parents=True, exist_ok=True)
pathlib.Path('/root/.buncker/config.json').write_text(json.dumps(config, indent=2))
print('OK: synced')
"
check "buncker-fetch config synced" \
    exec_online test -f /root/.buncker/config.json

# Fetch manifest from Docker Hub (online has internet)
bold "  -- Step 3: Fetch manifest from Docker Hub --"
exec_online python3 /tmp/fetch-manifest.py 2>&1
check "manifest fetched to /transfer/" \
    exec_online test -f /transfer/alpine-3.19-manifest.json

# Inject manifest into buncker store (copy via docker cp)
bold "  -- Step 4: Inject manifest into buncker store --"
$COMPOSE cp online:/transfer/alpine-3.19-manifest.json ./alpine-manifest.json
$COMPOSE cp ./alpine-manifest.json buncker-offline:/tmp/manifest.json
rm -f ./alpine-manifest.json

exec_offline python3 /tmp/inject-manifest.py /tmp/manifest.json docker.io library/alpine 3.19 linux-amd64 2>&1
check "manifest injected into store" \
    exec_offline test -f /var/lib/buncker/manifests/docker.io/library/alpine/3.19/linux-amd64.json

# Analyze Dockerfile
bold "  -- Step 5: Analyze and generate manifest --"
exec_offline bash -c "echo 'FROM alpine:3.19' > /tmp/test.Dockerfile"
ANALYZE_OUTPUT=$(exec_offline buncker analyze /tmp/test.Dockerfile 2>&1) || true
echo "  Analyze: $ANALYZE_OUTPUT"
check_output "analyze found missing blobs" "missing_blobs" echo "$ANALYZE_OUTPUT"

exec_offline buncker generate-manifest --output /tmp/ 2>&1 || true
REQUEST_FILE="/tmp/buncker-request.json.enc"
check "transfer request generated" \
    exec_offline test -f "$REQUEST_FILE"

# Transfer request to online side
bold "  -- Step 6: Transfer and fetch --"
$COMPOSE cp buncker-offline:"$REQUEST_FILE" ./request.json.enc
$COMPOSE cp ./request.json.enc online:/transfer/request.json.enc
rm -f ./request.json.enc

FETCH_OUTPUT=$(exec_online bash -c "echo '$MNEMONIC' | buncker-fetch fetch /transfer/request.json.enc --output /transfer/" 2>&1) || true
echo "  Fetch: $FETCH_OUTPUT"
RESPONSE_FILE=$(exec_online bash -c "ls -t /transfer/buncker-response-*.tar.enc 2>/dev/null | head -1")
check "response file generated" \
    test -n "$RESPONSE_FILE"

# Transfer response back and import
bold "  -- Step 7: Import blobs --"
$COMPOSE cp online:"$RESPONSE_FILE" ./response.tar.enc
$COMPOSE cp ./response.tar.enc buncker-offline:/tmp/response.tar.enc
rm -f ./response.tar.enc

IMPORT_OUTPUT=$(exec_offline buncker import /tmp/response.tar.enc 2>&1) || true
echo "  Import: $IMPORT_OUTPUT"
check_output "blobs imported successfully" "imported" echo "$IMPORT_OUTPUT"

# Verify OCI endpoints
bold "  -- Step 8: Verify OCI endpoints --"
check "OCI /v2/ returns ok" \
    exec_offline curl -sf http://127.0.0.1:5000/v2/

STATUS_OUTPUT=$(exec_offline curl -sf http://127.0.0.1:5000/admin/status)
echo "  Status: $STATUS_OUTPUT"
check_output "store has blobs" "blob_count" echo "$STATUS_OUTPUT"

# ---------------------------------------------------------------
# Phase 2: LAN Client Flow (API auth)
# ---------------------------------------------------------------

bold ""
bold "=== Phase 2: LAN Client Flow (API auth) ==="

# Stop daemon, run api-setup, restart with auth
bold "  -- Step 1: Enable API auth --"
exec_offline bash -c "kill \$(pgrep -f 'buncker serve') 2>/dev/null" || true
sleep 1

exec_offline buncker api-setup 2>&1 || true
check "api-tokens.json created" \
    exec_offline test -f /etc/buncker/api-tokens.json

ADMIN_TOKEN=$(exec_offline python3 -c "import json; print(json.load(open('/etc/buncker/api-tokens.json'))['admin'])")
RO_TOKEN=$(exec_offline python3 -c "import json; print(json.load(open('/etc/buncker/api-tokens.json'))['readonly'])")
echo "  Admin token: ${ADMIN_TOKEN:0:10}..."
echo "  RO token: ${RO_TOKEN:0:10}..."

# Restart daemon with auth enabled (now serves HTTPS)
exec_offline bash -c "nohup bash -c 'BUNCKER_MNEMONIC=\"$MNEMONIC\" buncker serve' > /tmp/buncker-auth.log 2>&1 &"
sleep 3

check "daemon restarted (HTTPS)" \
    exec_offline curl -ksf https://127.0.0.1:5000/v2/

# Test auth from client container (LAN client via curl)
bold "  -- Step 2: Test auth enforcement from LAN client --"
BUNCKER_URL="https://buncker-offline:5000"

# No token -> 401
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" "$BUNCKER_URL/admin/status")
check "no token -> 401 on /admin/status" \
    test "$HTTP_CODE" = "401"

# RO token -> 200 on status
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $RO_TOKEN" "$BUNCKER_URL/admin/status")
check "RO token -> 200 on /admin/status" \
    test "$HTTP_CODE" = "200"

# RO token -> 403 on analyze
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -X POST -H "Authorization: Bearer $RO_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"dockerfile_content":"FROM scratch\n"}' \
    "$BUNCKER_URL/admin/analyze")
check "RO token -> 403 on /admin/analyze" \
    test "$HTTP_CODE" = "403"

# OCI /v2/ -> 200 without token
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" "$BUNCKER_URL/v2/")
check "OCI /v2/ -> 200 without token" \
    test "$HTTP_CODE" = "200"

# Full LAN client cycle with admin token
bold "  -- Step 3: Full LAN client cycle (analyze -> generate -> fetch -> PUT import) --"

# Analyze via content mode
ANALYZE_RESULT=$(exec_client curl -ks \
    -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"dockerfile_content":"FROM alpine:3.19\nRUN apk add curl\n"}' \
    "$BUNCKER_URL/admin/analyze")
echo "  Analyze: $ANALYZE_RESULT"
check_output "LAN analyze found images" "images" echo "$ANALYZE_RESULT"

# Generate manifest (download encrypted request via curl)
exec_client bash -c "curl -ks \
    -X POST -H 'Authorization: Bearer $ADMIN_TOKEN' \
    -H 'Content-Type: application/octet-stream' \
    -H 'Content-Length: 0' \
    -o /transfer/lan-request.json.enc \
    '$BUNCKER_URL/admin/generate-manifest'"
check "LAN generate-manifest downloaded" \
    exec_client test -s /transfer/lan-request.json.enc

# Online side fetches blobs
bold "  -- Step 4: Online fetch for LAN request --"
FETCH_OUTPUT2=$(exec_online bash -c "echo '$MNEMONIC' | buncker-fetch fetch /transfer/lan-request.json.enc --output /transfer/" 2>&1) || true
echo "  Fetch: $FETCH_OUTPUT2"
LAN_RESPONSE=$(exec_online bash -c "ls -t /transfer/buncker-response-*.tar.enc 2>/dev/null | head -1")
check "LAN response file generated" \
    test -n "$LAN_RESPONSE"

# Copy response to client for PUT upload
$COMPOSE cp online:"$LAN_RESPONSE" ./lan-response.tar.enc
$COMPOSE cp ./lan-response.tar.enc client:/transfer/lan-response.tar.enc
rm -f ./lan-response.tar.enc

# PUT import with checksum from client
bold "  -- Step 5: PUT import from LAN client --"
CHECKSUM=$(exec_client sha256sum /transfer/lan-response.tar.enc | cut -d' ' -f1)
echo "  Checksum: sha256:$CHECKSUM"

# PUT without token -> 401
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -T /transfer/lan-response.tar.enc \
    -H "X-Buncker-Checksum: sha256:$CHECKSUM" \
    "$BUNCKER_URL/admin/import")
check "PUT import without token -> 401" \
    test "$HTTP_CODE" = "401"

# PUT with wrong checksum -> 400
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -T /transfer/lan-response.tar.enc \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "X-Buncker-Checksum: sha256:0000000000000000000000000000000000000000000000000000000000000000" \
    "$BUNCKER_URL/admin/import")
check "PUT import with wrong checksum -> 400" \
    test "$HTTP_CODE" = "400"

# PUT with admin token + correct checksum -> 200
IMPORT_RESULT=$(exec_client curl -ks -w "\n%{http_code}" \
    -T /transfer/lan-response.tar.enc \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "X-Buncker-Checksum: sha256:$CHECKSUM" \
    "$BUNCKER_URL/admin/import")
HTTP_CODE=$(echo "$IMPORT_RESULT" | tail -1)
IMPORT_BODY=$(echo "$IMPORT_RESULT" | sed '$d')
echo "  Import result: $IMPORT_BODY"
check "PUT import with admin token -> 200" \
    test "$HTTP_CODE" = "200"

# Verify status via RO token
STATUS=$(exec_client curl -ks \
    -H "Authorization: Bearer $RO_TOKEN" \
    "$BUNCKER_URL/admin/status")
echo "  Status: $STATUS"
check_output "store has blobs after LAN import" "blob_count" echo "$STATUS"

# Token reset
bold "  -- Step 6: Token reset --"
exec_offline buncker api-reset admin 2>&1 || true
NEW_ADMIN_TOKEN=$(exec_offline python3 -c "import json; print(json.load(open('/etc/buncker/api-tokens.json'))['admin'])")

check "admin token changed after reset" \
    test "$NEW_ADMIN_TOKEN" != "$ADMIN_TOKEN"

# Reload tokens (reset may have changed admin)
ADMIN_TOKEN=$(exec_offline python3 -c "import json; print(json.load(open('/etc/buncker/api-tokens.json'))['admin'])")
RO_TOKEN=$(exec_offline python3 -c "import json; print(json.load(open('/etc/buncker/api-tokens.json'))['readonly'])")

# ---------------------------------------------------------------
# Phase 3: OCI Restricted Mode (--restrict-oci)
# ---------------------------------------------------------------

bold ""
bold "=== Phase 3: OCI Restricted Mode (--restrict-oci) ==="

# Stop daemon and restart with --restrict-oci
bold "  -- Step 1: Restart daemon with --restrict-oci --"
exec_offline bash -c "kill \$(pgrep -f 'buncker serve') 2>/dev/null" || true
sleep 1

exec_offline bash -c "nohup bash -c 'BUNCKER_MNEMONIC=\"$MNEMONIC\" buncker serve --restrict-oci' > /tmp/buncker-restrict.log 2>&1 &"
sleep 3

# OCI /v2/ without token -> 401
bold "  -- Step 2: Test OCI auth enforcement --"
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" "$BUNCKER_URL/v2/")
check "OCI /v2/ without token -> 401 (restricted)" \
    test "$HTTP_CODE" = "401"

# OCI /v2/ with WWW-Authenticate header
WWW_AUTH=$(exec_client curl -ks -D - -o /dev/null "$BUNCKER_URL/v2/" | grep -i "WWW-Authenticate" || true)
check "OCI /v2/ returns WWW-Authenticate: Bearer header" \
    echo "$WWW_AUTH" | grep -q "Bearer"

# OCI /v2/ with readonly token -> 200
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $RO_TOKEN" "$BUNCKER_URL/v2/")
check "OCI /v2/ with RO token -> 200 (restricted)" \
    test "$HTTP_CODE" = "200"

# OCI /v2/ with admin token -> 200
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $ADMIN_TOKEN" "$BUNCKER_URL/v2/")
check "OCI /v2/ with admin token -> 200 (restricted)" \
    test "$HTTP_CODE" = "200"

# OCI manifest without token -> 401
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    "$BUNCKER_URL/v2/docker.io/library/alpine/manifests/3.19")
check "OCI manifest without token -> 401 (restricted)" \
    test "$HTTP_CODE" = "401"

# OCI manifest with RO token -> 200 or 404 (depends on cache)
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $RO_TOKEN" \
    "$BUNCKER_URL/v2/docker.io/library/alpine/manifests/3.19")
check "OCI manifest with RO token -> auth accepted (restricted)" \
    test "$HTTP_CODE" != "401"

# OCI blobs without token -> 401
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    "$BUNCKER_URL/v2/docker.io/library/alpine/blobs/sha256:0000000000000000000000000000000000000000000000000000000000000000")
check "OCI blob without token -> 401 (restricted)" \
    test "$HTTP_CODE" = "401"

# HEAD on OCI endpoint without token -> 401
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" -I \
    "$BUNCKER_URL/v2/docker.io/library/alpine/blobs/sha256:0000000000000000000000000000000000000000000000000000000000000000")
check "OCI HEAD without token -> 401 (restricted)" \
    test "$HTTP_CODE" = "401"

# Admin endpoints still require admin token (unchanged)
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $RO_TOKEN" "$BUNCKER_URL/admin/status")
check "admin /status with RO token -> 200 (unchanged)" \
    test "$HTTP_CODE" = "200"

# Invalid token on OCI -> 401
HTTP_CODE=$(exec_client curl -ks -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer invalid_token_12345" "$BUNCKER_URL/v2/")
check "OCI /v2/ with invalid token -> 401 (restricted)" \
    test "$HTTP_CODE" = "401"

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------

bold ""
bold "=== Results ==="
echo "  Total: $TOTAL"
green "  Passed: $PASS"
if [ "$FAIL" -gt 0 ]; then
    red "  Failed: $FAIL"
    exit 1
else
    green "  All tests passed!"
fi
