#!/bin/bash
# Verification script for Buncker .deb packages.
# Runs inside a Debian 12 container after dpkg -i.
set -euo pipefail

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo "  PASS  $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Buncker .deb verification ==="
echo ""

# --- Binaries ---
echo "[binaries]"
check "buncker exists"        test -x /usr/bin/buncker
check "buncker-fetch exists"  test -x /usr/bin/buncker-fetch
check "buncker --help"        /usr/bin/buncker --help
check "buncker-fetch --help"  /usr/bin/buncker-fetch --help

# --- Python modules ---
echo ""
echo "[modules]"
check "buncker module dir"       test -d /usr/lib/buncker/buncker
check "buncker __main__.py"      test -f /usr/lib/buncker/buncker/__main__.py
check "buncker shared dir"       test -d /usr/lib/buncker/shared
check "buncker-fetch module dir" test -d /usr/lib/buncker-fetch/buncker_fetch
check "buncker-fetch __main__.py" test -f /usr/lib/buncker-fetch/buncker_fetch/__main__.py
check "buncker-fetch shared dir" test -d /usr/lib/buncker-fetch/shared

# --- Config ---
echo ""
echo "[config]"
check "config dir exists"     test -d /etc/buncker
check "config.json exists"    test -f /etc/buncker/config.json
check "config.json is valid JSON" python3 -c "import json; json.load(open('/etc/buncker/config.json'))"

# --- Systemd ---
echo ""
echo "[systemd]"
check "buncker.service exists" test -f /lib/systemd/system/buncker.service

# --- postinst results ---
echo ""
echo "[postinst]"
check "buncker group exists"   getent group buncker
check "buncker user exists"    getent passwd buncker
check "/var/lib/buncker exists" test -d /var/lib/buncker
check "/var/log/buncker exists" test -d /var/log/buncker

# --- Functional: buncker setup ---
echo ""
echo "[functional]"

# Run setup with a temporary config path (default already exists from package)
SETUP_DIR=$(mktemp -d)
SETUP_CONFIG="$SETUP_DIR/config.json"
SETUP_STORE="$SETUP_DIR/store"

SETUP_OUTPUT=$(/usr/bin/buncker --config "$SETUP_CONFIG" setup --store-path "$SETUP_STORE" 2>&1)
SETUP_RC=$?

if [ $SETUP_RC -eq 0 ]; then
    echo "  PASS  buncker setup exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL  buncker setup exits 0 (got $SETUP_RC)"
    FAIL=$((FAIL + 1))
fi

# Extract mnemonic from setup output
MNEMONIC=$(echo "$SETUP_OUTPUT" | grep -E '^\s+\w+' | head -1 | xargs)
WORD_COUNT=$(echo "$MNEMONIC" | wc -w)

if [ "$WORD_COUNT" -eq 12 ]; then
    echo "  PASS  setup generates 12-word mnemonic"
    PASS=$((PASS + 1))
else
    echo "  FAIL  setup generates 12-word mnemonic (got $WORD_COUNT words)"
    FAIL=$((FAIL + 1))
fi

check "setup creates config" test -f "$SETUP_CONFIG"
check "setup config is valid JSON" python3 -c "import json; json.load(open('$SETUP_CONFIG'))"
check "setup creates store dir" test -d "$SETUP_STORE"

# --- Functional: buncker-fetch pair ---
# Feed the mnemonic from setup into buncker-fetch pair
FETCH_CONFIG="$SETUP_DIR/fetch-config.json"

PAIR_OUTPUT=$(echo "$MNEMONIC" | /usr/bin/buncker-fetch --config "$FETCH_CONFIG" pair 2>&1)
PAIR_RC=$?

if [ $PAIR_RC -eq 0 ]; then
    echo "  PASS  buncker-fetch pair exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL  buncker-fetch pair exits 0 (got $PAIR_RC)"
    FAIL=$((FAIL + 1))
fi

check "pair creates config" test -f "$FETCH_CONFIG"
check "pair config has salt" python3 -c "import json; c=json.load(open('$FETCH_CONFIG')); assert 'salt' in c"
check "pair config has key check" python3 -c "import json; c=json.load(open('$FETCH_CONFIG')); assert 'derived_key_check' in c"

# --- Daemon: serve + analyze workflow ---
echo ""
echo "[daemon]"

# Start daemon in background with the mnemonic
export BUNCKER_MNEMONIC="$MNEMONIC"
/usr/bin/buncker --config "$SETUP_CONFIG" serve > /dev/null 2>&1 &
DAEMON_PID=$!

# Wait for daemon to be ready (up to 5 seconds)
READY=0
for i in $(seq 1 50); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/v2/')" > /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.1
done

if [ "$READY" -eq 1 ]; then
    echo "  PASS  daemon starts and responds on /v2/"
    PASS=$((PASS + 1))
else
    echo "  FAIL  daemon starts and responds on /v2/"
    FAIL=$((FAIL + 1))
    kill "$DAEMON_PID" 2>/dev/null || true
    rm -rf "$SETUP_DIR"
    echo ""
    echo "=== Results: $PASS passed, $FAIL failed ==="
    exit 1
fi

# Check status endpoint
STATUS_FILE="$SETUP_DIR/status.json"
/usr/bin/buncker --config "$SETUP_CONFIG" status > "$STATUS_FILE" 2>&1
STATUS_RC=$?

if [ $STATUS_RC -eq 0 ]; then
    echo "  PASS  buncker status exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL  buncker status exits 0 (got $STATUS_RC)"
    FAIL=$((FAIL + 1))
fi

check "status returns valid JSON" python3 -c "import json; json.load(open('$STATUS_FILE'))"
check "status has version and blob_count" python3 -c "
import json
d = json.load(open('$STATUS_FILE'))
assert 'version' in d, 'missing version'
assert 'blob_count' in d, 'missing blob_count'
"

# Create a test Dockerfile and analyze it
TEST_DOCKERFILE="$SETUP_DIR/Dockerfile"
cat > "$TEST_DOCKERFILE" << 'DKEOF'
FROM python:3.11-slim
RUN pip install flask
DKEOF

ANALYZE_FILE="$SETUP_DIR/analyze.json"
/usr/bin/buncker --config "$SETUP_CONFIG" analyze "$TEST_DOCKERFILE" > "$ANALYZE_FILE" 2>&1
ANALYZE_RC=$?

if [ $ANALYZE_RC -eq 0 ]; then
    echo "  PASS  buncker analyze exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL  buncker analyze exits 0 (got $ANALYZE_RC)"
    FAIL=$((FAIL + 1))
fi

check "analyze returns valid JSON" python3 -c "import json; json.load(open('$ANALYZE_FILE'))"
check "analyze has source_path" python3 -c "import json; d=json.load(open('$ANALYZE_FILE')); assert 'source_path' in d"
check "analyze has images list" python3 -c "import json; d=json.load(open('$ANALYZE_FILE')); assert isinstance(d.get('images'), list)"
check "analyze has missing_blobs" python3 -c "import json; d=json.load(open('$ANALYZE_FILE')); assert 'missing_blobs' in d"
check "analyze detects python:3.11-slim" python3 -c "
import json
d = json.load(open('$ANALYZE_FILE'))
imgs = [i['raw'] for i in d['images']]
assert any('python' in i and '3.11-slim' in i for i in imgs), f'images: {imgs}'
"

# Stop daemon
kill "$DAEMON_PID" 2>/dev/null || true
wait "$DAEMON_PID" 2>/dev/null || true

# Cleanup
rm -rf "$SETUP_DIR"

# --- Summary ---
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
