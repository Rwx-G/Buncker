#!/bin/bash
# Build .rpm packages inside a Fedora container.
# Usage: ./scripts/build-in-docker-rpm.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Building .rpm packages in Fedora container..."
docker run --rm \
    -v "$PROJECT_ROOT:/src:ro" \
    -v "$PROJECT_ROOT/dist:/dist" \
    -w /src \
    fedora:latest \
    bash -c "
        dnf install -y rpm-build python3 &&
        make build-rpm DIST=/dist
    "

echo "Done. RPMs in dist/"
ls -la "$PROJECT_ROOT/dist/"*.rpm 2>/dev/null || echo "No .rpm files found"
