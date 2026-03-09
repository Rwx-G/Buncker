"""Tests for buncker.resolver - Dockerfile Parser (Story 2.4)."""

import pytest

from buncker.resolver import parse_dockerfile, resolve_dockerfile
from shared.exceptions import ResolverError


class TestSimpleDockerfile:
    """Tests for basic single-stage Dockerfiles."""

    def test_single_from(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\n")

        images = parse_dockerfile(df)

        assert len(images) == 1
        img = images[0]
        assert img.registry == "docker.io"
        assert img.repository == "library/nginx"
        assert img.tag == "1.25"
        assert img.digest is None
        assert img.is_internal is False

    def test_default_tag_latest(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx\n")

        images = parse_dockerfile(df)

        assert images[0].tag == "latest"
        assert "latest" in images[0].resolved

    def test_docker_hub_normalization_bare(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx\n")

        images = parse_dockerfile(df)

        assert images[0].registry == "docker.io"
        assert images[0].repository == "library/nginx"

    def test_docker_hub_normalization_org(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM myorg/myimage\n")

        images = parse_dockerfile(df)

        assert images[0].registry == "docker.io"
        assert images[0].repository == "myorg/myimage"

    def test_explicit_registry(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM ghcr.io/owner/repo:v1\n")

        images = parse_dockerfile(df)

        assert images[0].registry == "ghcr.io"
        assert images[0].repository == "owner/repo"
        assert images[0].tag == "v1"

    def test_registry_with_port_and_tag(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM localhost:5000/myapp:v2\n")

        images = parse_dockerfile(df)

        assert images[0].registry == "localhost:5000"
        assert images[0].repository == "myapp"
        assert images[0].tag == "v2"

    def test_file_not_found(self, tmp_path):
        with pytest.raises(ResolverError, match="not found"):
            parse_dockerfile(tmp_path / "missing")


class TestMultiStage:
    """Tests for multi-stage builds with aliases."""

    def test_multi_stage_with_alias(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "FROM golang:1.21 AS builder\n"
            "RUN go build\n"
            "FROM alpine:3.19\n"
            "COPY --from=builder /app /app\n"
        )

        images = parse_dockerfile(df)

        assert len(images) == 2
        assert images[0].alias == "builder"
        assert images[1].alias is None

    def test_internal_alias_detected(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM golang:1.21 AS builder\nFROM builder AS final\n")

        images = parse_dockerfile(df)

        assert images[0].is_internal is False
        assert images[1].is_internal is True


class TestArgs:
    """Tests for ARG substitution."""

    def test_arg_with_default(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "ARG BASE_IMAGE=nginx\nARG VERSION=1.25\nFROM ${BASE_IMAGE}:${VERSION}\n"
        )

        images = parse_dockerfile(df)

        assert images[0].repository == "library/nginx"
        assert images[0].tag == "1.25"

    def test_arg_override(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("ARG VERSION=1.25\nFROM nginx:${VERSION}\n")

        images = parse_dockerfile(
            df,
            build_args={"VERSION": "1.26"},
        )

        assert images[0].tag == "1.26"

    def test_arg_no_default_raises(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("ARG VERSION\nFROM nginx:$VERSION\n")

        with pytest.raises(ResolverError, match="no default"):
            parse_dockerfile(df)

    def test_arg_no_default_with_override(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("ARG VERSION\nFROM nginx:$VERSION\n")

        images = parse_dockerfile(
            df,
            build_args={"VERSION": "1.25"},
        )

        assert images[0].tag == "1.25"

    def test_undefined_arg_raises(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:$UNDEFINED\n")

        with pytest.raises(ResolverError, match="not defined"):
            parse_dockerfile(df)

    def test_arg_default_fallback(self, tmp_path):
        """${VAR:-default} uses fallback when VAR is unset."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:${VERSION:-1.25}\n")

        images = parse_dockerfile(df)
        assert images[0].tag == "1.25"

    def test_arg_default_not_used_when_set(self, tmp_path):
        """${VAR:-default} uses VAR value when defined."""
        df = tmp_path / "Dockerfile"
        df.write_text("ARG VERSION=1.26\nFROM nginx:${VERSION:-1.25}\n")

        images = parse_dockerfile(df)
        assert images[0].tag == "1.26"

    def test_arg_default_with_build_arg_override(self, tmp_path):
        """${VAR:-default} uses build-arg when provided."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:${VERSION:-1.25}\n")

        images = parse_dockerfile(df, build_args={"VERSION": "1.27"})
        assert images[0].tag == "1.27"

    def test_arg_default_empty_string_uses_fallback(self, tmp_path):
        """${VAR:-default} uses fallback when VAR is empty string."""
        df = tmp_path / "Dockerfile"
        df.write_text('ARG VERSION=""\nFROM nginx:${VERSION:-1.25}\n')

        images = parse_dockerfile(df)
        assert images[0].tag == "1.25"

    def test_arg_replacement_when_set(self, tmp_path):
        """${VAR:+replacement} uses replacement when VAR is set."""
        df = tmp_path / "Dockerfile"
        df.write_text("ARG USE_ALPINE=yes\nFROM ${USE_ALPINE:+alpine}:3.19\n")

        images = parse_dockerfile(df)
        assert images[0].repository == "library/alpine"

    def test_arg_replacement_when_unset(self, tmp_path):
        """${VAR:+replacement} returns empty when VAR is unset."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx${SUFFIX:+-slim}:1.25\n")

        images = parse_dockerfile(df)
        assert images[0].repository == "library/nginx"


class TestPlatform:
    """Tests for --platform flag."""

    def test_platform_extraction(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM --platform=linux/arm64 nginx:1.25\n")

        images = parse_dockerfile(df)

        assert images[0].platform == "linux/arm64"
        assert images[0].repository == "library/nginx"
        assert images[0].tag == "1.25"

    def test_platform_with_arg_variable(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "ARG TARGETPLATFORM=linux/amd64\n"
            "FROM --platform=${TARGETPLATFORM} nginx:1.25\n"
        )

        images = parse_dockerfile(df)

        assert images[0].platform == "linux/amd64"

    def test_platform_arg_override(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "ARG TARGETPLATFORM=linux/amd64\n"
            "FROM --platform=${TARGETPLATFORM} nginx:1.25\n"
        )

        images = parse_dockerfile(
            df,
            build_args={"TARGETPLATFORM": "linux/arm64"},
        )

        assert images[0].platform == "linux/arm64"

    def test_platform_with_variant(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM --platform=linux/arm/v7 nginx:1.25\n")

        images = parse_dockerfile(df)

        assert images[0].platform == "linux/arm/v7"


class TestDigest:
    """Tests for digest references."""

    def test_digest_extraction(self, tmp_path):
        digest = "sha256:" + "a" * 64
        df = tmp_path / "Dockerfile"
        df.write_text(f"FROM nginx@{digest}\n")

        images = parse_dockerfile(df)

        assert images[0].digest == digest
        assert images[0].tag is None

    def test_digest_resolved_string(self, tmp_path):
        digest = "sha256:" + "a" * 64
        df = tmp_path / "Dockerfile"
        df.write_text(f"FROM nginx@{digest}\n")

        images = parse_dockerfile(df)

        assert f"@{digest}" in images[0].resolved


class TestScratch:
    """Tests for FROM scratch."""

    def test_scratch_skipped(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "FROM golang:1.21 AS builder\nFROM scratch\nCOPY --from=builder /app /app\n"
        )

        images = parse_dockerfile(df)

        assert len(images) == 1
        assert images[0].repository == "library/golang"


class TestContinuationAndComments:
    """Tests for backslash continuation and comments."""

    def test_continuation(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM \\\n  nginx:1.25\n")

        images = parse_dockerfile(df)

        assert images[0].repository == "library/nginx"
        assert images[0].tag == "1.25"

    def test_comments_ignored(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("# This is a comment\nFROM nginx:1.25\n# Another comment\n")

        images = parse_dockerfile(df)

        assert len(images) == 1

    def test_empty_dockerfile(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("# Only comments\n")

        images = parse_dockerfile(df)

        assert images == []


class TestPrivateRegistries:
    """Tests for private registry detection."""

    def test_private_exact_match(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM registry.internal/myapp:v1\n")

        images = parse_dockerfile(
            df,
            private_registries=["registry.internal"],
        )

        assert images[0].is_private is True

    def test_private_wildcard_port(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM localhost:5000/myapp:v1\n")

        images = parse_dockerfile(
            df,
            private_registries=["localhost:*"],
        )

        assert images[0].is_private is True

    def test_public_not_private(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\n")

        images = parse_dockerfile(
            df,
            private_registries=["registry.internal"],
        )

        assert images[0].is_private is False


class TestLineNumber:
    """Tests for line number tracking."""

    def test_line_numbers(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text(
            "# Comment\n"
            "ARG VERSION=1.25\n"
            "FROM nginx:${VERSION}\n"
            "RUN echo hello\n"
            "FROM alpine:3.19\n"
        )

        images = parse_dockerfile(df)

        assert images[0].line_number == 3
        assert images[1].line_number == 5


# ======================================================================
# Story 2.5 - Resolver Pipeline
# ======================================================================


LAYER_A = "sha256:" + "a" * 64
LAYER_B = "sha256:" + "b" * 64
LAYER_C = "sha256:" + "c" * 64
CONFIG_D = "sha256:" + "d" * 64

MANIFEST_NGINX = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": CONFIG_D,
        "size": 100,
    },
    "layers": [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": LAYER_A,
            "size": 1000,
        },
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": LAYER_B,
            "size": 2000,
        },
    ],
    "_buncker": {
        "cached_at": "2026-03-04T10:00:00Z",
        "source_digest": "sha256:" + "e" * 64,
    },
}

MANIFEST_ALPINE = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.oci.image.manifest.v1+json",
    "config": {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": CONFIG_D,
        "size": 100,
    },
    "layers": [
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": LAYER_A,
            "size": 1000,
        },
        {
            "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            "digest": LAYER_C,
            "size": 3000,
        },
    ],
    "_buncker": {
        "cached_at": "2026-03-04T10:00:00Z",
        "source_digest": "sha256:" + "f" * 64,
    },
}


class FakeCache:
    """Fake ManifestCache for testing."""

    def __init__(self, manifests=None):
        self._manifests = manifests or {}

    def get_manifest(self, registry, repository, reference, platform):
        key = f"{registry}/{repository}:{reference}:{platform}"
        return self._manifests.get(key)


class FakeStore:
    """Fake Store for testing."""

    def __init__(self, present=None):
        self._present = set(present or [])

    def list_missing(self, digests):
        return [d for d in digests if d not in self._present]


class TestResolveDockerfile:
    """Tests for resolve_dockerfile pipeline."""

    def test_full_pipeline(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\n")

        cache = FakeCache(
            {
                "docker.io/library/nginx:1.25:linux/amd64": MANIFEST_NGINX,
            }
        )
        store = FakeStore(present=[LAYER_A])

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        assert len(result.images) == 1
        assert LAYER_A in result.present_blobs
        assert len(result.missing_blobs) == 2  # LAYER_B + CONFIG_D
        missing_digests = {b["digest"] for b in result.missing_blobs}
        assert LAYER_B in missing_digests
        assert CONFIG_D in missing_digests

    def test_deduplication_shared_blobs(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\nFROM alpine:3.19\n")

        cache = FakeCache(
            {
                "docker.io/library/nginx:1.25:linux/amd64": MANIFEST_NGINX,
                "docker.io/library/alpine:3.19:linux/amd64": MANIFEST_ALPINE,
            }
        )
        store = FakeStore()

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        all_digests = [b["digest"] for b in result.missing_blobs]
        # LAYER_A and CONFIG_D are shared - counted once
        assert all_digests.count(LAYER_A) == 1
        assert all_digests.count(CONFIG_D) == 1

    def test_private_image_skipped(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM registry.internal/myapp:v1\n")

        cache = FakeCache()
        store = FakeStore()

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
            private_registries=["registry.internal"],
        )

        assert any("skipped" in w.lower() for w in result.warnings)
        assert result.missing_blobs == []

    def test_missing_manifest_warning(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\n")

        cache = FakeCache()  # empty cache
        store = FakeStore()

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        assert any("not cached" in w.lower() for w in result.warnings)
        assert result.missing_blobs == []

    def test_latest_tag_warning(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx\n")

        cache = FakeCache()
        store = FakeStore()

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        assert any("latest" in w for w in result.warnings)

    def test_all_present(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\n")

        cache = FakeCache(
            {
                "docker.io/library/nginx:1.25:linux/amd64": MANIFEST_NGINX,
            }
        )
        store = FakeStore(present=[LAYER_A, LAYER_B, CONFIG_D])

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        assert result.missing_blobs == []
        assert result.total_missing_size == 0
        assert len(result.present_blobs) == 3

    def test_total_missing_size(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\n")

        cache = FakeCache(
            {
                "docker.io/library/nginx:1.25:linux/amd64": MANIFEST_NGINX,
            }
        )
        store = FakeStore()

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        # CONFIG_D=100 + LAYER_A=1000 + LAYER_B=2000
        assert result.total_missing_size == 3100

    def test_find_layer_info_not_found(self):
        """_find_layer_info returns {} for unknown digest."""
        from buncker.resolver import _find_layer_info

        manifest = {
            "config": {"digest": "sha256:config", "size": 100},
            "layers": [{"digest": "sha256:layer1", "size": 200}],
        }
        assert _find_layer_info(manifest, "sha256:unknown") == {}

    def test_build_resolved_no_tag_no_digest(self):
        """_build_resolved with no tag and no digest returns base only."""
        from buncker.resolver import _build_resolved

        result = _build_resolved("docker.io", "library/x", None, None)
        assert result == "docker.io/library/x"

    def test_deduplication_skips_already_seen(self, tmp_path):
        """Two identical FROM lines don't produce duplicate missing blobs."""
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx:1.25\nFROM nginx:1.25\n")

        cache = FakeCache(
            {
                "docker.io/library/nginx:1.25:linux/amd64": MANIFEST_NGINX,
            }
        )
        store = FakeStore()

        result = resolve_dockerfile(
            df,
            store=store,
            registry_client=cache,
        )

        # Blobs counted only once despite 2 FROM lines
        all_digests = [b["digest"] for b in result.missing_blobs]
        assert all_digests.count(LAYER_A) == 1
        assert all_digests.count(LAYER_B) == 1
