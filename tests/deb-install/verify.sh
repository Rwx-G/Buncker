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

# --- Analyze: simple Dockerfile ---
TEST_DOCKERFILE="$SETUP_DIR/Dockerfile.simple"
cat > "$TEST_DOCKERFILE" << 'DKEOF'
FROM python:3.11-slim
RUN pip install flask
DKEOF

ANALYZE_FILE="$SETUP_DIR/analyze-simple.json"
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

# --- Analyze: multi-stage Dockerfile with ARG ---
echo ""
echo "[analyze-multistage]"

TEST_DOCKERFILE_MS="$SETUP_DIR/Dockerfile.multistage"
cat > "$TEST_DOCKERFILE_MS" << 'DKEOF'
ARG NODE_VERSION=20
FROM node:${NODE_VERSION}-bookworm-slim AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM debian:12-slim AS runtime
COPY --from=builder /app/node_modules /app/node_modules
CMD ["node", "/app/index.js"]
DKEOF

ANALYZE_FILE_MS="$SETUP_DIR/analyze-multi.json"
/usr/bin/buncker --config "$SETUP_CONFIG" analyze "$TEST_DOCKERFILE_MS" \
    --build-arg NODE_VERSION=22 > "$ANALYZE_FILE_MS" 2>&1
ANALYZE_MS_RC=$?

if [ $ANALYZE_MS_RC -eq 0 ]; then
    echo "  PASS  analyze multi-stage exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL  analyze multi-stage exits 0 (got $ANALYZE_MS_RC)"
    FAIL=$((FAIL + 1))
fi

check "multi-stage returns valid JSON" python3 -c "import json; json.load(open('$ANALYZE_FILE_MS'))"
check "multi-stage detects 2 external images" python3 -c "
import json
d = json.load(open('$ANALYZE_FILE_MS'))
external = [i for i in d['images'] if not i.get('is_internal', False)]
assert len(external) == 2, f'expected 2 external images, got {len(external)}: {external}'
"
check "multi-stage applies ARG substitution (node:22)" python3 -c "
import json
d = json.load(open('$ANALYZE_FILE_MS'))
imgs = [i['resolved'] for i in d['images']]
assert any('node' in i and '22' in i for i in imgs), f'expected node:22 in {imgs}'
"
check "multi-stage detects debian:12-slim" python3 -c "
import json
d = json.load(open('$ANALYZE_FILE_MS'))
imgs = [i['resolved'] for i in d['images']]
assert any('debian' in i and '12-slim' in i for i in imgs), f'expected debian:12-slim in {imgs}'
"
check "multi-stage has warnings (no manifest cache)" python3 -c "
import json
d = json.load(open('$ANALYZE_FILE_MS'))
assert len(d.get('warnings', [])) > 0, 'expected warnings for uncached manifests'
"

# --- Full cycle: inject manifest cache, generate-manifest, buncker-fetch inspect ---
echo ""
echo "[transfer-cycle]"

# Inject a fake OCI manifest into the cache so analyze finds missing blobs
FAKE_CONFIG="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
FAKE_LAYER_1="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FAKE_LAYER_2="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

python3 << PYEOF
import json
from pathlib import Path

store = Path("$SETUP_STORE")
cache_dir = store / "manifests" / "docker.io" / "library/nginx" / "1.25"
cache_dir.mkdir(parents=True, exist_ok=True)

manifest = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": "$FAKE_CONFIG",
        "size": 1234
    },
    "layers": [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": "$FAKE_LAYER_1",
            "size": 31457280
        },
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": "$FAKE_LAYER_2",
            "size": 5242880
        }
    ],
    "_buncker": {
        "cached_at": "2026-03-04T00:00:00+00:00",
        "source_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    }
}

(cache_dir / "linux-amd64.json").write_text(json.dumps(manifest, indent=2))
print("Injected fake nginx:1.25 manifest (config + 2 layers)")
PYEOF

# Analyze a Dockerfile that uses the cached image
TEST_DOCKERFILE_NGINX="$SETUP_DIR/Dockerfile.nginx"
cat > "$TEST_DOCKERFILE_NGINX" << 'DKEOF'
FROM nginx:1.25
COPY index.html /usr/share/nginx/html/
DKEOF

ANALYZE_NGINX="$SETUP_DIR/analyze-nginx.json"
/usr/bin/buncker --config "$SETUP_CONFIG" analyze "$TEST_DOCKERFILE_NGINX" > "$ANALYZE_NGINX" 2>&1

check "analyze with cache returns valid JSON" python3 -c "import json; json.load(open('$ANALYZE_NGINX'))"
check "analyze finds 3 missing blobs (config + 2 layers)" python3 -c "
import json
d = json.load(open('$ANALYZE_NGINX'))
assert len(d['missing_blobs']) == 3, f'expected 3 missing, got {len(d[\"missing_blobs\"])}'
"
check "missing blobs have correct digests" python3 -c "
import json
d = json.load(open('$ANALYZE_NGINX'))
digests = sorted([b['digest'] for b in d['missing_blobs']])
expected = sorted(['$FAKE_CONFIG', '$FAKE_LAYER_1', '$FAKE_LAYER_2'])
assert digests == expected, f'expected {expected}, got {digests}'
"
check "missing blobs have size info" python3 -c "
import json
d = json.load(open('$ANALYZE_NGINX'))
total = sum(b['size'] for b in d['missing_blobs'])
assert total == 36701394, f'expected 36701394 bytes total, got {total}'
"

# Generate encrypted transfer request (CLI saves to CWD)
cd "$SETUP_DIR"
GENMAN_OUTPUT=$(/usr/bin/buncker --config "$SETUP_CONFIG" generate-manifest 2>&1)
GENMAN_RC=$?

if [ $GENMAN_RC -eq 0 ]; then
    echo "  PASS  generate-manifest exits 0"
    PASS=$((PASS + 1))
else
    echo "  FAIL  generate-manifest exits 0 (got $GENMAN_RC)"
    echo "        output: $GENMAN_OUTPUT"
    FAIL=$((FAIL + 1))
fi

# CLI saves as buncker-request.json.enc in CWD
ENC_FILE="$SETUP_DIR/buncker-request.json.enc"
if [ -f "$ENC_FILE" ]; then
    echo "  PASS  request .json.enc file created"
    PASS=$((PASS + 1))
    check "request file is non-empty" test -s "$ENC_FILE"

    # Create a fetch config that shares the daemon's salt so keys match.
    # In production, both sides derive keys from the same mnemonic + salt.
    # The pair command generates its own salt (for local key verification),
    # so for the transfer test we copy the daemon's salt into the fetch config.
    TRANSFER_FETCH_CONFIG="$SETUP_DIR/fetch-transfer.json"
    python3 << PYEOF
import json, base64
daemon_cfg = json.load(open("$SETUP_CONFIG"))
salt = daemon_cfg["crypto"]["salt"]
# Derive keys and create derived_key_check with daemon salt
import sys
sys.path.insert(0, "/usr/lib/buncker-fetch")
from shared.crypto import derive_keys, encrypt
aes_key, hmac_key = derive_keys("$MNEMONIC", base64.b64decode(salt))
marker = b"buncker-pair-check"
derived_key_check = base64.b64encode(encrypt(marker, aes_key)).decode()
fetch_cfg = {"salt": salt, "derived_key_check": derived_key_check}
with open("$TRANSFER_FETCH_CONFIG", "w") as f:
    json.dump(fetch_cfg, f, indent=2)
PYEOF

    # Use buncker-fetch inspect to decrypt and validate.
    # input("> ") writes the prompt to stdout, so strip it before parsing JSON.
    INSPECT_RAW="$SETUP_DIR/inspect-raw.txt"
    INSPECT_FILE="$SETUP_DIR/inspect.json"
    echo "$MNEMONIC" | /usr/bin/buncker-fetch --json --config "$TRANSFER_FETCH_CONFIG" \
        inspect "$ENC_FILE" > "$INSPECT_RAW" 2>/dev/null
    INSPECT_RC=$?
    # Remove the "> " prompt prefix from input() that leaks into stdout
    sed 's/^> //' "$INSPECT_RAW" > "$INSPECT_FILE"

    if [ $INSPECT_RC -eq 0 ]; then
        echo "  PASS  buncker-fetch inspect exits 0"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  buncker-fetch inspect exits 0 (got $INSPECT_RC)"
        FAIL=$((FAIL + 1))
    fi

    check "inspect returns valid JSON" python3 -c "import json; json.load(open('$INSPECT_FILE'))"
    check "inspect has blob_count" python3 -c "
import json
d = json.load(open('$INSPECT_FILE'))
assert d.get('blob_count') == 3, f'expected 3 blobs, got {d.get(\"blob_count\")}'
"
    check "inspect has total_size" python3 -c "
import json
d = json.load(open('$INSPECT_FILE'))
assert d.get('total_size') == 36701394, f'expected 36701394, got {d.get(\"total_size\")}'
"
    check "inspect has source_id" python3 -c "
import json
d = json.load(open('$INSPECT_FILE'))
assert d.get('source_id', '') != '', 'source_id should not be empty'
"
    check "inspect has registries" python3 -c "
import json
d = json.load(open('$INSPECT_FILE'))
assert 'docker.io' in d.get('registries', []), f'expected docker.io in {d.get(\"registries\")}'
"

else
    echo "  FAIL  request .json.enc file created"
    FAIL=$((FAIL + 1))
fi

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
