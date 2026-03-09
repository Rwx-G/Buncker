"""Tests for shared.oci module."""

import hashlib
import json
from pathlib import Path

import pytest

from shared.oci import (
    OCIIndex,
    OCIIndexEntry,
    OCILayer,
    OCIManifest,
    OCIPlatform,
    build_image_layout,
    parse_index,
    parse_manifest,
    select_platform,
    verify_blob,
)

# ---------------------------------------------------------------------------
# Fixtures: simplified real-world manifests
# ---------------------------------------------------------------------------

HELLO_WORLD_MANIFEST = json.dumps(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:aaaa",
            "size": 100,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": "sha256:bbbb",
                "size": 200,
            }
        ],
    }
)

NGINX_MANIFEST = json.dumps(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:cccc",
            "size": 300,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": "sha256:dddd",
                "size": 400,
            },
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                "digest": "sha256:eeee",
                "size": 500,
            },
        ],
    }
)

MULTI_PLATFORM_INDEX = json.dumps(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": "sha256:amd64manifest",
                "size": 500,
                "platform": {"architecture": "amd64", "os": "linux"},
            },
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": "sha256:arm64manifest",
                "size": 500,
                "platform": {"architecture": "arm64", "os": "linux", "variant": "v8"},
            },
        ],
    }
)


# ---------------------------------------------------------------------------
# parse_manifest tests
# ---------------------------------------------------------------------------


class TestParseManifest:
    def test_parse_hello_world(self) -> None:
        m = parse_manifest(HELLO_WORLD_MANIFEST)
        assert m.schema_version == 2
        assert m.config.digest == "sha256:aaaa"
        assert len(m.layers) == 1
        assert m.layers[0].digest == "sha256:bbbb"
        assert m.digest.startswith("sha256:")

    def test_parse_nginx_multiple_layers(self) -> None:
        m = parse_manifest(NGINX_MANIFEST)
        assert len(m.layers) == 2
        assert m.layers[0].digest == "sha256:dddd"
        assert m.layers[1].digest == "sha256:eeee"

    def test_parse_bytes_input(self) -> None:
        m = parse_manifest(HELLO_WORLD_MANIFEST.encode())
        assert m.schema_version == 2

    def test_digest_is_deterministic(self) -> None:
        m1 = parse_manifest(HELLO_WORLD_MANIFEST)
        m2 = parse_manifest(HELLO_WORLD_MANIFEST)
        assert m1.digest == m2.digest

    def test_missing_field_raises_value_error(self) -> None:
        bad = json.dumps({"schemaVersion": 2})
        with pytest.raises(ValueError, match="Missing required field"):
            parse_manifest(bad)

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_manifest("not json {{{")

    def test_empty_layers(self) -> None:
        data = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": "sha256:abc",
                    "size": 10,
                },
                "layers": [],
            }
        )
        m = parse_manifest(data)
        assert m.layers == []


# ---------------------------------------------------------------------------
# parse_index tests
# ---------------------------------------------------------------------------


class TestParseIndex:
    def test_parse_multi_platform(self) -> None:
        idx = parse_index(MULTI_PLATFORM_INDEX)
        assert idx.schema_version == 2
        assert len(idx.manifests) == 2
        assert idx.manifests[0].platform is not None
        assert idx.manifests[0].platform.architecture == "amd64"
        assert idx.manifests[1].platform is not None
        assert idx.manifests[1].platform.variant == "v8"

    def test_parse_bytes_input(self) -> None:
        idx = parse_index(MULTI_PLATFORM_INDEX.encode())
        assert len(idx.manifests) == 2

    def test_missing_field_raises_value_error(self) -> None:
        bad = json.dumps({"schemaVersion": 2})
        with pytest.raises(ValueError, match="Missing required field"):
            parse_index(bad)

    def test_entry_without_platform(self) -> None:
        data = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": "sha256:abc",
                        "size": 100,
                    }
                ],
            }
        )
        idx = parse_index(data)
        assert idx.manifests[0].platform is None


# ---------------------------------------------------------------------------
# select_platform tests
# ---------------------------------------------------------------------------


class TestSelectPlatform:
    def test_match_amd64(self) -> None:
        idx = parse_index(MULTI_PLATFORM_INDEX)
        digest = select_platform(idx, OCIPlatform("amd64", "linux"))
        assert digest == "sha256:amd64manifest"

    def test_match_arm64_with_variant(self) -> None:
        idx = parse_index(MULTI_PLATFORM_INDEX)
        digest = select_platform(idx, OCIPlatform("arm64", "linux", "v8"))
        assert digest == "sha256:arm64manifest"

    def test_no_match_raises_value_error(self) -> None:
        idx = parse_index(MULTI_PLATFORM_INDEX)
        with pytest.raises(ValueError, match="No manifest found"):
            select_platform(idx, OCIPlatform("s390x", "linux"))

    def test_entry_without_platform_is_skipped(self) -> None:
        """Index entry without 'platform' key should be skipped."""
        idx = OCIIndex(
            schema_version=2,
            media_type="application/vnd.oci.image.index.v1+json",
            manifests=[
                OCIIndexEntry(
                    media_type="application/vnd.oci.image.manifest.v1+json",
                    digest="sha256:noplatform",
                    size=100,
                    platform=None,
                ),
                OCIIndexEntry(
                    media_type="application/vnd.oci.image.manifest.v1+json",
                    digest="sha256:amd64result",
                    size=200,
                    platform=OCIPlatform("amd64", "linux"),
                ),
            ],
        )
        digest = select_platform(idx, OCIPlatform("amd64", "linux"))
        assert digest == "sha256:amd64result"


# ---------------------------------------------------------------------------
# verify_blob tests
# ---------------------------------------------------------------------------


class TestVerifyBlob:
    def test_correct_digest(self, tmp_path: Path) -> None:
        content = b"hello blob"
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        blob = tmp_path / "blob"
        blob.write_bytes(content)
        assert verify_blob(blob, digest) is True

    def test_incorrect_digest(self, tmp_path: Path) -> None:
        blob = tmp_path / "blob"
        blob.write_bytes(b"content")
        assert verify_blob(blob, "sha256:0000") is False


# ---------------------------------------------------------------------------
# build_image_layout tests
# ---------------------------------------------------------------------------


class TestBuildImageLayout:
    def _make_blob(self, blobs_dir: Path, content: bytes) -> str:
        """Create a blob file and return its digest."""
        digest_hex = hashlib.sha256(content).hexdigest()
        (blobs_dir / digest_hex).write_bytes(content)
        return f"sha256:{digest_hex}"

    def test_produces_valid_layout(self, tmp_path: Path) -> None:
        blobs_dir = tmp_path / "src_blobs"
        blobs_dir.mkdir()
        output_dir = tmp_path / "layout"

        config_content = b'{"architecture":"amd64"}'
        layer_content = b"layer data bytes"
        config_digest = self._make_blob(blobs_dir, config_content)
        layer_digest = self._make_blob(blobs_dir, layer_content)

        manifest_json = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {
                    "mediaType": "application/vnd.oci.image.config.v1+json",
                    "digest": config_digest,
                    "size": len(config_content),
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                        "digest": layer_digest,
                        "size": len(layer_content),
                    }
                ],
            }
        )
        manifest = parse_manifest(manifest_json)

        # Also store manifest blob
        manifest_bytes = manifest_json.encode()
        self._make_blob(blobs_dir, manifest_bytes)

        index = OCIIndex(
            schema_version=2,
            media_type="application/vnd.oci.image.index.v1+json",
            manifests=[
                OCIIndexEntry(
                    media_type="application/vnd.oci.image.manifest.v1+json",
                    digest=manifest.digest,
                    size=len(manifest_bytes),
                    platform=OCIPlatform("amd64", "linux"),
                )
            ],
        )

        build_image_layout(blobs_dir, [manifest], index, output_dir)

        # Verify structure
        assert (output_dir / "oci-layout").exists()
        oci_layout = json.loads((output_dir / "oci-layout").read_text())
        assert oci_layout["imageLayoutVersion"] == "1.0.0"
        assert (output_dir / "index.json").exists()
        assert (output_dir / "blobs" / "sha256").is_dir()

        # Verify blobs were copied
        config_hex = config_digest.removeprefix("sha256:")
        assert (output_dir / "blobs" / "sha256" / config_hex).exists()

    def test_missing_blob_raises(self, tmp_path: Path) -> None:
        blobs_dir = tmp_path / "empty"
        blobs_dir.mkdir()
        output_dir = tmp_path / "layout"

        manifest = OCIManifest(
            schema_version=2,
            media_type="application/vnd.oci.image.manifest.v1+json",
            config=OCILayer(
                "application/vnd.oci.image.config.v1+json", "sha256:missing", 10
            ),
            layers=[],
            digest="sha256:manifestdigest",
        )
        index = OCIIndex(
            schema_version=2,
            media_type="application/vnd.oci.image.index.v1+json",
        )

        with pytest.raises(FileNotFoundError, match="Blob not found"):
            build_image_layout(blobs_dir, [manifest], index, output_dir)

    def test_blob_digest_mismatch_raises(self, tmp_path: Path) -> None:
        """Blob with wrong content raises ValueError."""
        blobs_dir = tmp_path / "src"
        blobs_dir.mkdir()
        output_dir = tmp_path / "layout"

        # Create a blob file whose name doesn't match its content hash
        fake_hex = "a" * 64
        (blobs_dir / fake_hex).write_bytes(b"wrong content")

        # Also create a valid manifest blob so build_image_layout reaches config
        manifest_content = b'{"schemaVersion": 2}'
        manifest_hex = hashlib.sha256(manifest_content).hexdigest()
        (blobs_dir / manifest_hex).write_bytes(manifest_content)

        manifest = OCIManifest(
            schema_version=2,
            media_type="application/vnd.oci.image.manifest.v1+json",
            config=OCILayer(
                "application/vnd.oci.image.config.v1+json",
                f"sha256:{fake_hex}",
                10,
            ),
            layers=[],
            digest=f"sha256:{manifest_hex}",
        )
        index = OCIIndex(
            schema_version=2,
            media_type="application/vnd.oci.image.index.v1+json",
        )

        with pytest.raises(ValueError, match="digest mismatch"):
            build_image_layout(blobs_dir, [manifest], index, output_dir)


class TestBuildIndexJson:
    def test_index_entry_with_variant(self) -> None:
        """Platform with variant 'v8' serialises correctly."""
        from shared.oci import _build_index_json

        index = OCIIndex(
            schema_version=2,
            media_type="application/vnd.oci.image.index.v1+json",
            manifests=[
                OCIIndexEntry(
                    media_type="application/vnd.oci.image.manifest.v1+json",
                    digest="sha256:arm64",
                    size=300,
                    platform=OCIPlatform("arm64", "linux", "v8"),
                )
            ],
        )
        data = json.loads(_build_index_json(index))
        platform = data["manifests"][0]["platform"]
        assert platform["variant"] == "v8"
        assert platform["architecture"] == "arm64"
