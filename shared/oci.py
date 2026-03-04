"""OCI Image Layout parsing and building primitives."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class OCIPlatform:
    """Target platform for an OCI image."""

    architecture: str
    os: str
    variant: str | None = None


@dataclass(frozen=True)
class OCILayer:
    """Descriptor for a blob (config, layer, or manifest)."""

    media_type: str
    digest: str
    size: int


@dataclass(frozen=True)
class OCIManifest:
    """OCI Image Manifest v2."""

    schema_version: int
    media_type: str
    config: OCILayer
    layers: list[OCILayer]
    digest: str


@dataclass(frozen=True)
class OCIIndexEntry:
    """Single entry in an OCI Image Index."""

    media_type: str
    digest: str
    size: int
    platform: OCIPlatform | None = None


@dataclass(frozen=True)
class OCIIndex:
    """OCI Image Index (multi-platform)."""

    schema_version: int
    media_type: str
    manifests: list[OCIIndexEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_manifest(data: bytes | str) -> OCIManifest:
    """Parse OCI v2 manifest JSON into an OCIManifest dataclass.

    Args:
        data: Raw JSON bytes or string.

    Returns:
        Parsed OCIManifest with computed digest.

    Raises:
        ValueError: If required fields are missing or invalid.
        json.JSONDecodeError: If data is not valid JSON.
    """
    raw = data if isinstance(data, bytes) else data.encode()
    doc = json.loads(raw)

    for key in ("schemaVersion", "mediaType", "config", "layers"):
        if key not in doc:
            raise ValueError(f"Missing required field: {key}")

    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    config = _parse_layer(doc["config"])
    layers = [_parse_layer(layer) for layer in doc["layers"]]

    return OCIManifest(
        schema_version=doc["schemaVersion"],
        media_type=doc["mediaType"],
        config=config,
        layers=layers,
        digest=digest,
    )


def parse_index(data: bytes | str) -> OCIIndex:
    """Parse OCI Image Index JSON into an OCIIndex dataclass.

    Args:
        data: Raw JSON bytes or string.

    Returns:
        Parsed OCIIndex.

    Raises:
        ValueError: If required fields are missing.
        json.JSONDecodeError: If data is not valid JSON.
    """
    raw = data if isinstance(data, bytes) else data.encode()
    doc = json.loads(raw)

    for key in ("schemaVersion", "mediaType", "manifests"):
        if key not in doc:
            raise ValueError(f"Missing required field: {key}")

    entries = []
    for m in doc["manifests"]:
        platform = None
        if "platform" in m:
            p = m["platform"]
            platform = OCIPlatform(
                architecture=p["architecture"],
                os=p["os"],
                variant=p.get("variant"),
            )
        entries.append(
            OCIIndexEntry(
                media_type=m["mediaType"],
                digest=m["digest"],
                size=m["size"],
                platform=platform,
            )
        )

    return OCIIndex(
        schema_version=doc["schemaVersion"],
        media_type=doc["mediaType"],
        manifests=entries,
    )


# ---------------------------------------------------------------------------
# Platform selection
# ---------------------------------------------------------------------------


def select_platform(index: OCIIndex, platform: OCIPlatform) -> str:
    """Find the manifest digest matching the requested platform.

    Args:
        index: Parsed OCI Image Index.
        platform: Target platform to match.

    Returns:
        Digest string of the matching manifest.

    Raises:
        ValueError: If no matching platform is found.
    """
    for entry in index.manifests:
        if entry.platform is None:
            continue
        if (
            entry.platform.architecture == platform.architecture
            and entry.platform.os == platform.os
            and (platform.variant is None or entry.platform.variant == platform.variant)
        ):
            return entry.digest

    raise ValueError(
        f"No manifest found for platform {platform.os}/{platform.architecture}"
        + (f"/{platform.variant}" if platform.variant else "")
    )


# ---------------------------------------------------------------------------
# Image layout building
# ---------------------------------------------------------------------------

_OCI_LAYOUT = '{"imageLayoutVersion":"1.0.0"}\n'


def build_image_layout(
    blobs_dir: Path,
    manifests: list[OCIManifest],
    index: OCIIndex,
    output_dir: Path,
) -> None:
    """Build a valid OCI Image Layout directory.

    Args:
        blobs_dir: Source directory containing blobs named by digest.
        manifests: List of parsed manifests to include.
        index: The OCI Image Index referencing the manifests.
        output_dir: Destination directory for the OCI layout.

    Raises:
        FileNotFoundError: If a required blob is missing.
        ValueError: If a blob digest does not match.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # oci-layout file
    (output_dir / "oci-layout").write_text(_OCI_LAYOUT)

    # blobs/sha256/ directory
    blobs_out = output_dir / "blobs" / "sha256"
    blobs_out.mkdir(parents=True, exist_ok=True)

    # Collect all digests needed
    digests: set[str] = set()
    for manifest in manifests:
        digests.add(manifest.config.digest)
        for layer in manifest.layers:
            digests.add(layer.digest)
        digests.add(manifest.digest)

    # Copy blobs and verify
    for digest in digests:
        hash_hex = digest.removeprefix("sha256:")
        src = blobs_dir / hash_hex
        if not src.exists():
            raise FileNotFoundError(f"Blob not found: {digest}")
        if not verify_blob(src, digest):
            raise ValueError(f"Blob digest mismatch: {digest}")
        dst = blobs_out / hash_hex
        if not dst.exists():
            dst.write_bytes(src.read_bytes())

    # index.json
    index_data = _build_index_json(index)
    (output_dir / "index.json").write_bytes(index_data)


def _build_index_json(index: OCIIndex) -> bytes:
    """Serialize an OCIIndex to JSON bytes."""
    manifests = []
    for entry in index.manifests:
        m: dict = {
            "mediaType": entry.media_type,
            "digest": entry.digest,
            "size": entry.size,
        }
        if entry.platform is not None:
            p: dict = {
                "architecture": entry.platform.architecture,
                "os": entry.platform.os,
            }
            if entry.platform.variant is not None:
                p["variant"] = entry.platform.variant
            m["platform"] = p
        manifests.append(m)

    doc = {
        "schemaVersion": index.schema_version,
        "mediaType": index.media_type,
        "manifests": manifests,
    }
    return json.dumps(doc, indent=2).encode()


# ---------------------------------------------------------------------------
# Blob verification
# ---------------------------------------------------------------------------


def verify_blob(path: Path, expected_digest: str) -> bool:
    """Verify a blob file matches its expected SHA256 digest.

    Args:
        path: Path to the blob file.
        expected_digest: Expected digest in "sha256:<hex>" format.

    Returns:
        True if digest matches, False otherwise.
    """
    hash_hex = expected_digest.removeprefix("sha256:")
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest() == hash_hex


def _parse_layer(doc: dict) -> OCILayer:
    """Parse a descriptor dict into an OCILayer."""
    return OCILayer(
        media_type=doc["mediaType"],
        digest=doc["digest"],
        size=doc["size"],
    )
