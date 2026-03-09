"""Tests for buncker.store - Store Core (Story 2.1) & GC (Story 2.2)."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from buncker.store import Store
from shared.exceptions import StoreError


def _digest(data: bytes) -> str:
    """Compute sha256:<hex> digest for test data."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class TestStoreInit:
    """Tests for Store.__init__ - directory structure creation."""

    def test_creates_directory_structure(self, tmp_path):
        Store(tmp_path)

        assert (tmp_path / "blobs" / "sha256").is_dir()
        assert (tmp_path / "meta" / "sha256").is_dir()
        assert (tmp_path / "oci-layout").exists()
        assert (tmp_path / "index.json").exists()

    def test_oci_layout_content(self, tmp_path):
        Store(tmp_path)

        layout = json.loads((tmp_path / "oci-layout").read_text())
        assert layout == {"imageLayoutVersion": "1.0.0"}

    def test_index_json_content(self, tmp_path):
        Store(tmp_path)

        index = json.loads((tmp_path / "index.json").read_text())
        assert index == {"schemaVersion": 2, "manifests": []}

    def test_idempotent_reinit(self, tmp_path):
        store1 = Store(tmp_path)
        data = b"hello world"
        digest = _digest(data)
        store1.import_blob(data, digest)

        # Re-init should not overwrite existing files
        Store(tmp_path)

        assert (tmp_path / "oci-layout").exists()
        assert (tmp_path / "index.json").exists()
        assert store1.has_blob(digest)


class TestImportBlob:
    """Tests for Store.import_blob."""

    def test_import_valid_blob(self, tmp_path):
        store = Store(tmp_path)
        data = b"test blob content"
        digest = _digest(data)

        path = store.import_blob(data, digest)

        assert path.exists()
        assert path.read_bytes() == data

    def test_import_creates_sidecar(self, tmp_path):
        store = Store(tmp_path)
        data = b"test blob content"
        digest = _digest(data)

        store.import_blob(data, digest, image_ref="docker.io/library/nginx:1.25")

        meta = store.get_metadata(digest)
        assert meta["digest"] == digest
        assert meta["size"] == len(data)
        assert meta["image_refs"] == ["docker.io/library/nginx:1.25"]
        assert meta["gc_status"] == "active"
        assert "first_imported" in meta
        assert "last_requested" in meta

    def test_import_digest_mismatch_raises(self, tmp_path):
        store = Store(tmp_path)
        data = b"test blob content"
        wrong_digest = "sha256:" + "a" * 64

        with pytest.raises(StoreError, match="Digest mismatch"):
            store.import_blob(data, wrong_digest)

    def test_import_digest_mismatch_no_leftover(self, tmp_path):
        store = Store(tmp_path)
        data = b"test blob content"
        wrong_digest = "sha256:" + "a" * 64

        with pytest.raises(StoreError):
            store.import_blob(data, wrong_digest)

        # No blob file should be left behind
        blobs = list((tmp_path / "blobs" / "sha256").iterdir())
        assert blobs == []

    def test_idempotent_reimport(self, tmp_path):
        store = Store(tmp_path)
        data = b"test blob content"
        digest = _digest(data)

        path1 = store.import_blob(data, digest, image_ref="nginx:1.25")
        path2 = store.import_blob(data, digest, image_ref="nginx:1.25")

        assert path1 == path2
        assert path1.read_bytes() == data

    def test_reimport_adds_new_image_ref(self, tmp_path):
        store = Store(tmp_path)
        data = b"shared layer"
        digest = _digest(data)

        store.import_blob(data, digest, image_ref="nginx:1.25")
        store.import_blob(data, digest, image_ref="alpine:3.19")

        meta = store.get_metadata(digest)
        assert "nginx:1.25" in meta["image_refs"]
        assert "alpine:3.19" in meta["image_refs"]


class TestHasBlob:
    """Tests for Store.has_blob."""

    def test_has_blob_returns_true(self, tmp_path):
        store = Store(tmp_path)
        data = b"existing blob"
        digest = _digest(data)
        store.import_blob(data, digest)

        assert store.has_blob(digest) is True

    def test_has_blob_returns_false(self, tmp_path):
        store = Store(tmp_path)

        assert store.has_blob("sha256:" + "b" * 64) is False


class TestGetBlob:
    """Tests for Store.get_blob."""

    def test_get_blob_returns_path(self, tmp_path):
        store = Store(tmp_path)
        data = b"my blob"
        digest = _digest(data)
        store.import_blob(data, digest)

        path = store.get_blob(digest)

        assert path.read_bytes() == data

    def test_get_blob_raises_when_absent(self, tmp_path):
        store = Store(tmp_path)

        with pytest.raises(StoreError, match="Blob not found"):
            store.get_blob("sha256:" + "c" * 64)


class TestListMissing:
    """Tests for Store.list_missing."""

    def test_all_present(self, tmp_path):
        store = Store(tmp_path)
        data = b"present blob"
        digest = _digest(data)
        store.import_blob(data, digest)

        assert store.list_missing([digest]) == []

    def test_all_missing(self, tmp_path):
        store = Store(tmp_path)
        missing = ["sha256:" + "d" * 64, "sha256:" + "e" * 64]

        assert store.list_missing(missing) == missing

    def test_mixed_present_and_missing(self, tmp_path):
        store = Store(tmp_path)
        data = b"present blob"
        digest = _digest(data)
        store.import_blob(data, digest)
        absent = "sha256:" + "f" * 64

        result = store.list_missing([digest, absent])

        assert result == [absent]


class TestMetadata:
    """Tests for metadata sidecar management."""

    def test_update_metadata(self, tmp_path):
        store = Store(tmp_path)
        data = b"meta test"
        digest = _digest(data)
        store.import_blob(data, digest)

        meta_before = store.get_metadata(digest)
        count_before = meta_before["request_count"]

        store.update_metadata(digest, "blob_served")

        meta_after = store.get_metadata(digest)
        assert meta_after["request_count"] == count_before + 1

    def test_update_metadata_raises_for_missing(self, tmp_path):
        store = Store(tmp_path)

        with pytest.raises(StoreError, match="Sidecar not found"):
            store.update_metadata("sha256:" + "a" * 64, "test_event")

    def test_sidecar_format(self, tmp_path):
        store = Store(tmp_path)
        data = b"format test"
        digest = _digest(data)
        mt = "application/vnd.oci.image.layer.v1.tar+gzip"
        store.import_blob(data, digest, media_type=mt)

        meta = store.get_metadata(digest)

        assert meta["digest"] == digest
        assert meta["size"] == len(data)
        assert meta["media_type"] == "application/vnd.oci.image.layer.v1.tar+gzip"
        assert isinstance(meta["image_refs"], list)
        assert meta["gc_status"] == "active"
        assert meta["request_count"] == 0

    def test_bare_hex_digest_accepted(self, tmp_path):
        store = Store(tmp_path)
        data = b"bare hex test"
        full_digest = _digest(data)
        bare_hex = full_digest.removeprefix("sha256:")

        store.import_blob(data, full_digest)

        assert store.has_blob(bare_hex)
        assert store.get_blob(bare_hex).read_bytes() == data


# ======================================================================
# Story 2.2 - Store GC
# ======================================================================


def _make_old_blob(store, data, *, days_old):
    """Import a blob and backdate its last_requested."""
    digest = _digest(data)
    store.import_blob(data, digest)
    # Manipulate sidecar to simulate age
    digest_hex = digest.removeprefix("sha256:")
    sidecar = store.path / "meta" / "sha256" / f"{digest_hex}.json"
    meta = json.loads(sidecar.read_text())
    old_ts = (datetime.now(tz=UTC) - timedelta(days=days_old)).isoformat()
    meta["last_requested"] = old_ts
    sidecar.write_text(json.dumps(meta, indent=2))
    return digest


class TestGcReport:
    """Tests for Store.gc_report."""

    def test_active_blob_not_candidate(self, tmp_path):
        store = Store(tmp_path)
        data = b"active blob"
        digest = _digest(data)
        store.import_blob(data, digest)

        candidates = store.gc_report(inactive_days=30)

        assert candidates == []

    def test_inactive_blob_is_candidate(self, tmp_path):
        store = Store(tmp_path)
        digest = _make_old_blob(store, b"old blob", days_old=60)

        candidates = store.gc_report(inactive_days=30)

        assert len(candidates) == 1
        assert candidates[0]["digest"] == digest

    def test_shared_blob_recent_is_protected(self, tmp_path):
        store = Store(tmp_path)
        data = b"shared layer"
        digest = _digest(data)
        store.import_blob(data, digest, image_ref="nginx:1.25")
        store.import_blob(data, digest, image_ref="alpine:3.19")

        candidates = store.gc_report(inactive_days=30)

        assert candidates == []

    def test_shared_blob_old_is_candidate(self, tmp_path):
        store = Store(tmp_path)
        data = b"shared old layer"
        digest = _make_old_blob(store, data, days_old=60)

        candidates = store.gc_report(inactive_days=30)

        assert len(candidates) == 1
        assert candidates[0]["digest"] == digest

    def test_report_contains_expected_fields(self, tmp_path):
        store = Store(tmp_path)
        _make_old_blob(store, b"field test", days_old=60)

        candidates = store.gc_report(inactive_days=30)

        c = candidates[0]
        assert "digest" in c
        assert "size" in c
        assert "last_requested" in c
        assert "image_refs" in c


class TestGcExecute:
    """Tests for Store.gc_execute."""

    def test_deletes_blob_and_sidecar(self, tmp_path):
        store = Store(tmp_path)
        digest = _make_old_blob(store, b"delete me", days_old=60)

        store.gc_report(inactive_days=30)
        result = store.gc_execute([digest], "admin")

        assert result["count"] == 1
        assert result["bytes_freed"] > 0
        assert not store.has_blob(digest)

    def test_refuses_digest_not_in_report(self, tmp_path):
        store = Store(tmp_path)
        _make_old_blob(store, b"some blob", days_old=60)
        store.gc_report(inactive_days=30)

        unknown = "sha256:" + "a" * 64
        with pytest.raises(StoreError, match="not in latest GC report"):
            store.gc_execute([unknown], "admin")

    def test_refuses_without_report(self, tmp_path):
        store = Store(tmp_path)
        digest = _digest(b"no report")

        with pytest.raises(StoreError, match="No GC report"):
            store.gc_execute([digest], "admin")

    def test_produces_log_entries(self, tmp_path, caplog):
        store = Store(tmp_path)
        digest = _make_old_blob(store, b"log test", days_old=60)

        with caplog.at_level("INFO", logger="buncker.store"):
            store.gc_report(inactive_days=30)
            store.gc_execute([digest], "test_operator")

        messages = [r.message for r in caplog.records]
        assert "gc_candidate" in messages
        assert "gc_executed" in messages


class TestStoreVerify:
    """Tests for Store.verify() - integrity check (bit-rot detection)."""

    def test_verify_empty_store(self, tmp_path):
        store = Store(tmp_path)
        result = store.verify()
        assert result["total"] == 0
        assert result["ok"] == 0
        assert result["corrupted"] == 0
        assert result["corrupted_digests"] == []

    def test_verify_healthy_blobs(self, tmp_path):
        store = Store(tmp_path)
        store.import_blob(b"hello", _digest(b"hello"))
        store.import_blob(b"world", _digest(b"world"))

        result = store.verify()
        assert result["total"] == 2
        assert result["ok"] == 2
        assert result["corrupted"] == 0

    def test_verify_detects_corrupted_blob(self, tmp_path):
        store = Store(tmp_path)
        data = b"original content"
        digest = _digest(data)
        store.import_blob(data, digest)

        # Corrupt the blob file
        digest_hex = digest.removeprefix("sha256:")
        blob_path = tmp_path / "blobs" / "sha256" / digest_hex
        blob_path.write_bytes(b"corrupted content")

        result = store.verify()
        assert result["total"] == 1
        assert result["ok"] == 0
        assert result["corrupted"] == 1
        assert digest in result["corrupted_digests"]

    def test_verify_mixed_healthy_and_corrupted(self, tmp_path):
        store = Store(tmp_path)
        good_data = b"good blob"
        bad_data = b"bad blob"
        store.import_blob(good_data, _digest(good_data))
        bad_digest = _digest(bad_data)
        store.import_blob(bad_data, bad_digest)

        # Corrupt only one blob
        bad_hex = bad_digest.removeprefix("sha256:")
        (tmp_path / "blobs" / "sha256" / bad_hex).write_bytes(b"tampered")

        result = store.verify()
        assert result["total"] == 2
        assert result["ok"] == 1
        assert result["corrupted"] == 1
        assert bad_digest in result["corrupted_digests"]

    def test_verify_logs_corrupted_blob(self, tmp_path, caplog):
        store = Store(tmp_path)
        data = b"test"
        digest = _digest(data)
        store.import_blob(data, digest)

        digest_hex = digest.removeprefix("sha256:")
        (tmp_path / "blobs" / "sha256" / digest_hex).write_bytes(b"bad")

        with caplog.at_level("ERROR", logger="buncker.store"):
            store.verify()

        messages = [r.message for r in caplog.records]
        assert "blob_corrupted" in messages


class TestGcImpactReport:
    """Tests for Store.gc_impact_report() - GC impact analysis."""

    def _make_manifest(self, store, registry, repo, tag, platform, blobs):
        """Create a cached manifest referencing given blob digests."""
        platform_file = platform.replace("/", "-") + ".json"
        manifest_dir = store.path / "manifests" / registry / repo / tag
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schemaVersion": 2,
            "config": {"digest": blobs[0], "size": 100} if blobs else {},
            "layers": [{"digest": d, "size": 200} for d in blobs[1:]],
        }
        (manifest_dir / platform_file).write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_no_manifests_returns_empty(self, tmp_path):
        store = Store(tmp_path)
        result = store.gc_impact_report(["sha256:" + "a" * 64])
        assert result == []

    def test_no_impact_when_no_overlap(self, tmp_path):
        store = Store(tmp_path)
        blob_data = b"config blob"
        digest = _digest(blob_data)
        store.import_blob(blob_data, digest)
        self._make_manifest(
            store,
            "docker.io",
            "library/alpine",
            "3.19",
            "linux/amd64",
            [digest],
        )

        # GC a different digest
        other = "sha256:" + "f" * 64
        result = store.gc_impact_report([other])
        assert result == []

    def test_detects_affected_image(self, tmp_path):
        store = Store(tmp_path)
        config_data = b"config"
        layer_data = b"layer"
        config_digest = _digest(config_data)
        layer_digest = _digest(layer_data)
        store.import_blob(config_data, config_digest)
        store.import_blob(layer_data, layer_digest)

        self._make_manifest(
            store,
            "docker.io",
            "library/nginx",
            "1.25",
            "linux/amd64",
            [config_digest, layer_digest],
        )

        result = store.gc_impact_report([layer_digest])
        assert len(result) == 1
        assert result[0]["image"] == "docker.io/library/nginx:1.25"
        assert result[0]["platform"] == "linux/amd64"
        assert result[0]["missing_count"] == 1
        assert layer_digest in result[0]["missing_blobs"]

    def test_multiple_images_affected(self, tmp_path):
        store = Store(tmp_path)
        shared = b"shared layer"
        shared_digest = _digest(shared)
        store.import_blob(shared, shared_digest)

        # Two images share the same blob
        self._make_manifest(
            store,
            "docker.io",
            "library/alpine",
            "3.19",
            "linux/amd64",
            [shared_digest],
        )
        self._make_manifest(
            store,
            "docker.io",
            "library/debian",
            "12",
            "linux/amd64",
            [shared_digest],
        )

        result = store.gc_impact_report([shared_digest])
        assert len(result) == 2
        images = {r["image"] for r in result}
        assert "docker.io/library/alpine:3.19" in images
        assert "docker.io/library/debian:12" in images
