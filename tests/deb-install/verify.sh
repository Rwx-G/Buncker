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

SETUP_OUTPUT=$(/usr/bin/buncker setup --config "$SETUP_CONFIG" --store-path "$SETUP_STORE" 2>&1)
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

PAIR_OUTPUT=$(echo "$MNEMONIC" | /usr/bin/buncker-fetch pair --config "$FETCH_CONFIG" 2>&1)
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

# Cleanup
rm -rf "$SETUP_DIR"

# --- Summary ---
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
