"""Tests for buncker.compose - Docker Compose file parser."""

from pathlib import Path

import pytest

from buncker.compose import ComposeService, parse_compose, parse_compose_content
from shared.exceptions import ResolverError


class TestParseCompose:
    """Tests for parse_compose() with file-based input."""

    def test_multi_service_image_only(self, tmp_path: Path) -> None:
        """Multi-service Compose with image: fields only."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  web:\n"
            "    image: nginx:1.25\n"
            "  db:\n"
            "    image: postgres:16\n"
            "  cache:\n"
            "    image: redis:7\n"
        )
        services = parse_compose(compose)
        assert len(services) == 3
        assert services[0] == ComposeService("web", "nginx:1.25", None, None)
        assert services[1] == ComposeService("db", "postgres:16", None, None)
        assert services[2] == ComposeService("cache", "redis:7", None, None)

    def test_build_with_dockerfile(self, tmp_path: Path) -> None:
        """Service with build.context and build.dockerfile."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    build:\n"
            "      context: ./app\n"
            "      dockerfile: Dockerfile.prod\n"
        )
        services = parse_compose(compose)
        assert len(services) == 1
        assert services[0].name == "app"
        assert services[0].image_ref is None
        expected = (tmp_path / "app" / "Dockerfile.prod").resolve()
        assert services[0].dockerfile_path == expected
        assert services[0].build_context == (tmp_path / "app").resolve()

    def test_build_context_only_defaults_to_dockerfile(self, tmp_path: Path) -> None:
        """Service with build.context but no dockerfile defaults to Dockerfile (AC4)."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    build:\n      context: ./src\n")
        services = parse_compose(compose)
        assert len(services) == 1
        expected = (tmp_path / "src").resolve() / "Dockerfile"
        assert services[0].dockerfile_path == expected

    def test_build_short_form(self, tmp_path: Path) -> None:
        """Service with build: ./path (short form)."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    build: ./myapp\n")
        services = parse_compose(compose)
        assert len(services) == 1
        expected = (tmp_path / "myapp").resolve() / "Dockerfile"
        assert services[0].dockerfile_path == expected

    def test_image_takes_priority_over_build(self, tmp_path: Path) -> None:
        """When both image: and build: exist, image: wins (AC3)."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    image: myorg/myapp:v2\n"
            "    build:\n"
            "      context: ./app\n"
        )
        services = parse_compose(compose)
        assert len(services) == 1
        assert services[0].image_ref == "myorg/myapp:v2"
        assert services[0].dockerfile_path is None

    def test_mixed_image_and_build(self, tmp_path: Path) -> None:
        """Mix of services with image and build."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  web:\n"
            "    image: nginx:1.25\n"
            "  app:\n"
            "    build:\n"
            "      context: ./app\n"
            "      dockerfile: Dockerfile.prod\n"
            "  db:\n"
            "    image: postgres:16\n"
        )
        services = parse_compose(compose)
        assert len(services) == 3
        assert services[0].image_ref == "nginx:1.25"
        assert services[1].image_ref is None
        assert services[1].dockerfile_path is not None
        assert services[2].image_ref == "postgres:16"

    def test_missing_services_key(self, tmp_path: Path) -> None:
        """Missing services: key returns actionable error (AC5)."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("version: '3'\n")
        with pytest.raises(ResolverError, match="no 'services' key"):
            parse_compose(compose)

    def test_empty_services(self, tmp_path: Path) -> None:
        """Empty services: returns actionable error (AC5)."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n")
        with pytest.raises(ResolverError, match="no 'services' key"):
            parse_compose(compose)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Invalid YAML returns actionable error."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  - not: valid: yaml: [[[\n")
        with pytest.raises(ResolverError, match="Invalid YAML"):
            parse_compose(compose)

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Non-existent file raises error."""
        with pytest.raises(ResolverError, match="not found"):
            parse_compose(tmp_path / "nonexistent.yml")

    def test_no_image_no_build_skipped(self, tmp_path: Path) -> None:
        """Service with neither image nor build is skipped."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  web:\n"
            "    image: nginx:1.25\n"
            "  volumes-only:\n"
            "    volumes:\n"
            "      - data:/data\n"
        )
        services = parse_compose(compose)
        assert len(services) == 1
        assert services[0].name == "web"


class TestParseComposeContent:
    """Tests for parse_compose_content() with string input (remote API)."""

    def test_image_services(self) -> None:
        """Extracts image: refs from content string."""
        content = (
            "services:\n  web:\n    image: nginx:1.25\n  db:\n    image: postgres:16\n"
        )
        services = parse_compose_content(content)
        assert len(services) == 2
        assert services[0].image_ref == "nginx:1.25"
        assert services[1].image_ref == "postgres:16"

    def test_build_services_skipped_in_content_mode(self) -> None:
        """Build services are skipped in content mode (no filesystem)."""
        content = "services:\n  app:\n    build:\n      context: ./app\n"
        services = parse_compose_content(content)
        assert len(services) == 0

    def test_invalid_yaml_content(self) -> None:
        """Invalid YAML content raises error."""
        with pytest.raises(ResolverError, match="Invalid YAML"):
            parse_compose_content("not: valid: yaml: [[[\n")

    def test_missing_services_content(self) -> None:
        """Missing services key in content raises error."""
        with pytest.raises(ResolverError, match="no 'services' key"):
            parse_compose_content("version: '3'\n")


class TestResolveCompose:
    """Tests for resolve_compose() integration with resolver."""

    def test_deduplication(self, tmp_path: Path) -> None:
        """Same image in multiple services counted once (AC7)."""
        from unittest.mock import MagicMock

        from buncker.compose import ComposeService
        from buncker.resolver import resolve_compose

        services = [
            ComposeService("web1", "nginx:1.25", None, None),
            ComposeService("web2", "nginx:1.25", None, None),
            ComposeService("db", "postgres:16", None, None),
        ]

        mock_store = MagicMock()
        mock_store.list_missing.return_value = []
        mock_cache = MagicMock()
        mock_cache.get_manifest.return_value = None

        result = resolve_compose(
            services,
            store=mock_store,
            registry_client=mock_cache,
        )

        # nginx:1.25 appears only once in images despite two services
        nginx_images = [img for img in result.images if "nginx" in img.resolved]
        assert len(nginx_images) == 1

    def test_compose_with_blobs(self, tmp_path: Path) -> None:
        """Compose resolve collects blobs from all services."""
        from unittest.mock import MagicMock

        from buncker.compose import ComposeService
        from buncker.resolver import resolve_compose

        services = [
            ComposeService("web", "nginx:1.25", None, None),
        ]

        mock_manifest = {
            "config": {"digest": "sha256:aaa", "size": 100},
            "layers": [
                {
                    "digest": "sha256:bbb",
                    "size": 200,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                },
            ],
        }

        mock_store = MagicMock()
        mock_store.list_missing.return_value = ["sha256:bbb"]
        mock_cache = MagicMock()
        mock_cache.get_manifest.return_value = mock_manifest

        result = resolve_compose(
            services,
            store=mock_store,
            registry_client=mock_cache,
        )

        assert len(result.missing_blobs) == 1
        assert result.missing_blobs[0]["digest"] == "sha256:bbb"
        assert "sha256:aaa" in result.present_blobs
