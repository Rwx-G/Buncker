"""Fetch alpine:3.19 linux/amd64 manifest from Docker Hub and save to /transfer/."""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

IMAGE = "alpine"
TAG = "3.19"
PLATFORM_OS = "linux"
PLATFORM_ARCH = "amd64"
OUTPUT = Path("/transfer/alpine-3.19-manifest.json")

# Get auth token
token_url = (
    "https://auth.docker.io/token"
    f"?service=registry.docker.io&scope=repository:library/{IMAGE}:pull"
)
token = json.loads(urllib.request.urlopen(token_url).read())["token"]

# Fetch manifest index
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": (
        "application/vnd.oci.image.index.v1+json, "
        "application/vnd.docker.distribution.manifest.list.v2+json"
    ),
}
req = urllib.request.Request(
    f"https://registry-1.docker.io/v2/library/{IMAGE}/manifests/{TAG}",
    headers=headers,
)
index = json.loads(urllib.request.urlopen(req).read())

# Find target platform manifest
target_digest = None
for m in index.get("manifests", []):
    p = m.get("platform", {})
    if p.get("os") == PLATFORM_OS and p.get("architecture") == PLATFORM_ARCH:
        annotations = m.get("annotations", {})
        if "attestation" not in annotations.get("vnd.docker.reference.type", ""):
            target_digest = m["digest"]
            break

if not target_digest:
    print(f"ERROR: no {PLATFORM_OS}/{PLATFORM_ARCH} manifest for {IMAGE}:{TAG}")
    sys.exit(1)

# Fetch platform manifest
req2 = urllib.request.Request(
    f"https://registry-1.docker.io/v2/library/{IMAGE}/manifests/{target_digest}",
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.manifest.v1+json",
    },
)
manifest = json.loads(urllib.request.urlopen(req2).read())

# Add buncker metadata
raw = json.dumps(
    {k: v for k, v in manifest.items() if k != "_buncker"}, sort_keys=True
).encode()
source_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
manifest["_buncker"] = {
    "cached_at": "2026-03-05T00:00:00+00:00",
    "source_digest": source_digest,
}

OUTPUT.write_text(json.dumps(manifest, indent=2))
layers = manifest.get("layers", [])
config_size = manifest.get("config", {}).get("size", 0)
total = sum(layer.get("size", 0) for layer in layers) + config_size
print(f"OK: {IMAGE}:{TAG} manifest saved ({len(layers)} layers + config, {total} bytes)")
