#!/bin/bash
# Run .deb installation tests locally using Docker.
# Usage: bash tests/deb-install/run.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE_NAME="buncker-deb-test"

echo "=== Building .deb packages ==="
make -C "$REPO_ROOT" build-deb

echo ""
echo "=== Building Docker test image ==="
docker build -t "$IMAGE_NAME" -f "$REPO_ROOT/tests/deb-install/Dockerfile" "$REPO_ROOT"

echo ""
echo "=== Running verification ==="
docker run --rm "$IMAGE_NAME"

echo ""
echo "=== Cleanup ==="
docker rmi "$IMAGE_NAME" > /dev/null 2>&1 || true
echo "Done."
