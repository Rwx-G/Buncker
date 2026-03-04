#!/bin/bash
# Build .deb packages inside Docker (for environments without dpkg-deb).
# Outputs to dist/ in the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

docker run --rm \
    -v "$REPO_ROOT:/src" \
    -w /src \
    debian:12-slim \
    sh -c "apt-get update && apt-get install -y --no-install-recommends python3 dpkg-dev && make build-deb"
