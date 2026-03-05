"""Inject a manifest JSON file into the buncker store's manifest cache."""

import json
import sys
from pathlib import Path

MANIFEST_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/manifest.json")
REGISTRY = sys.argv[2] if len(sys.argv) > 2 else "docker.io"
REPO = sys.argv[3] if len(sys.argv) > 3 else "library/alpine"
TAG = sys.argv[4] if len(sys.argv) > 4 else "3.19"
PLATFORM = sys.argv[5] if len(sys.argv) > 5 else "linux-amd64"

# Read buncker config to find store path
config = json.loads(Path("/etc/buncker/config.json").read_text())
store = Path(config.get("store_path", "/var/lib/buncker"))

# Create cache directory
cache_dir = store / "manifests" / REGISTRY / REPO / TAG
cache_dir.mkdir(parents=True, exist_ok=True)

# Copy manifest
manifest = json.loads(MANIFEST_FILE.read_text())
output = cache_dir / f"{PLATFORM}.json"
output.write_text(json.dumps(manifest, indent=2))

layers = manifest.get("layers", [])
config_size = manifest.get("config", {}).get("size", 0)
total = sum(layer.get("size", 0) for layer in layers) + config_size
print(f"OK: injected {REPO}:{TAG} into {cache_dir} ({len(layers)} layers, {total} bytes)")
