"""Tests for buncker_fetch.registry_client with mock HTTP server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from buncker_fetch.registry_client import (
    RegistryClient,
    _log_rate_limit_headers,
    _parse_retry_after,
    load_credentials,
)
from shared.exceptions import RegistryError


class MockRegistryHandler(BaseHTTPRequestHandler):
    """Mock OCI registry with Docker Hub-style auth."""

    # Class-level config
    require_auth = True
    fail_count = 0  # number of 500s before success
    _attempt_counter = 0
    rate_limit = False
    valid_token = "test-token-123"
    manifest_data = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:abc",
            "size": 100,
        },
        "layers": [],
    }
    blob_data = b"fake-blob-content-12345"

    def log_message(self, format, *args):  # noqa: A002
        pass  # Suppress logging in tests

    def do_GET(self):  # noqa: N802
        # Auth token endpoint
        if self.path.startswith("/token"):
            self._handle_token()
            return

        # /v2/ - version check / auth discovery
        if self.path == "/v2/":
            if self.require_auth and not self._check_bearer():
                self._send_401()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
            return

        # Manifest endpoint
        if "/manifests/" in self.path:
            if self.require_auth and not self._check_bearer():
                self._send_401()
                return
            if self.rate_limit:
                self.send_response(429)
                self.send_header("Retry-After", "30")
                self.end_headers()
                return
            if self._maybe_fail():
                return
            body = json.dumps(self.manifest_data).encode()
            self.send_response(200)
            ct = "application/vnd.oci.image.manifest.v1+json"
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Blob endpoint
        if "/blobs/" in self.path:
            if self.require_auth and not self._check_bearer():
                self._send_401()
                return
            if self._maybe_fail():
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(self.blob_data)))
            self.end_headers()
            self.wfile.write(self.blob_data)
            return

        self.send_response(404)
        self.end_headers()

    def _handle_token(self):
        # Accept both anonymous and authenticated
        token_data = {"token": self.valid_token}
        body = json.dumps(token_data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _send_401(self):
        self.send_response(401)
        realm = f"http://localhost:{self.server.server_address[1]}/token"
        scope = "repository:library/test:pull"
        www = f'Bearer realm="{realm}",service="test-registry",scope="{scope}"'
        self.send_header("Www-Authenticate", www)
        self.end_headers()

    def _check_bearer(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.valid_token}"

    def _maybe_fail(self) -> bool:
        """Return True if we should simulate a server error."""
        if MockRegistryHandler.fail_count > 0:
            MockRegistryHandler._attempt_counter += 1
            if MockRegistryHandler._attempt_counter <= MockRegistryHandler.fail_count:
                self.send_response(500)
                self.end_headers()
                return True
        return False


@pytest.fixture()
def mock_server():
    """Start a mock registry HTTP server."""
    # Reset class state
    MockRegistryHandler.require_auth = True
    MockRegistryHandler.fail_count = 0
    MockRegistryHandler._attempt_counter = 0
    MockRegistryHandler.rate_limit = False

    server = HTTPServer(("127.0.0.1", 0), MockRegistryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture()
def client(mock_server):
    """Create a RegistryClient pointing to the mock server."""
    host, port = mock_server.server_address
    # Patch to use HTTP instead of HTTPS for testing
    registry = f"127.0.0.1:{port}"
    client = RegistryClient(registry)
    return client


def _patch_https_to_http(client):
    """Patch the client to use http:// instead of https:// for local testing."""
    original_build = client._build_request

    def patched_build(path, scope, extra_headers=None):
        req = original_build(path, scope, extra_headers)
        req.full_url = req.full_url.replace("https://", "http://")
        return req

    client._build_request = patched_build

    # Also patch _get_token to use http
    def patched_get_token(scope):
        import urllib.error
        import urllib.request

        url = f"http://{client.registry}/v2/"
        req = urllib.request.Request(url, method="GET")
        try:
            response = urllib.request.urlopen(req, timeout=5)
            response.read()
            return None
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise RegistryError(
                    f"Auth discovery failed: HTTP {exc.code}",
                    {"url": url, "status": exc.code},
                ) from exc
            import re

            www_auth = exc.headers.get("Www-Authenticate", "")
            match = re.search(
                r'Bearer\s+realm="([^"]+)"'
                r'(?:,\s*service="([^"]*)")?'
                r'(?:,\s*scope="([^"]*)")?',
                www_auth,
                re.IGNORECASE,
            )
            if not match:
                raise RegistryError(
                    "Cannot parse Www-Authenticate header",
                    {"header": www_auth},
                ) from exc
            realm = match.group(1)
            service = match.group(2) or ""
            return client._exchange_token(realm, service, scope)

    client._get_token = patched_get_token


class TestAuthDiscovery:
    """Test auth discovery parses Www-Authenticate correctly."""

    def test_auth_discovery_with_bearer(self, mock_server, client):
        _patch_https_to_http(client)
        # Should discover auth and get a token
        manifest = client.fetch_manifest("library/test", "latest")
        assert manifest["schemaVersion"] == 2

    def test_no_auth_needed(self, mock_server, client):
        MockRegistryHandler.require_auth = False
        _patch_https_to_http(client)
        manifest = client.fetch_manifest("library/test", "latest")
        assert manifest["schemaVersion"] == 2


class TestTokenExchange:
    """Test token exchange."""

    def test_anonymous_token_exchange(self, mock_server, client):
        _patch_https_to_http(client)
        manifest = client.fetch_manifest("library/test", "latest")
        assert manifest is not None

    def test_authenticated_token_exchange(self, mock_server):
        host, port = mock_server.server_address
        client = RegistryClient(
            f"127.0.0.1:{port}",
            credentials={"username": "user", "password": "pass"},
        )
        _patch_https_to_http(client)
        manifest = client.fetch_manifest("library/test", "latest")
        assert manifest is not None


class TestFetchManifest:
    """Test fetch_manifest returns valid JSON."""

    def test_returns_valid_manifest(self, mock_server, client):
        _patch_https_to_http(client)
        manifest = client.fetch_manifest("library/nginx", "1.25")
        assert "schemaVersion" in manifest
        assert "mediaType" in manifest
        assert manifest["schemaVersion"] == 2


class TestFetchBlob:
    """Test fetch_blob streams data."""

    def test_streams_blob_data(self, mock_server, client):
        _patch_https_to_http(client)
        chunks = list(client.fetch_blob("library/test", "sha256:abc123"))
        data = b"".join(chunks)
        assert data == MockRegistryHandler.blob_data

    def test_blob_yields_chunks(self, mock_server, client):
        _patch_https_to_http(client)
        chunks = list(client.fetch_blob("library/test", "sha256:abc123"))
        assert len(chunks) >= 1


class TestRetry:
    """Test retry on 500 errors."""

    def test_retry_on_server_error(self, mock_server, client):
        MockRegistryHandler.fail_count = 1  # fail once, then succeed
        _patch_https_to_http(client)
        # Should succeed after retry - use sleep(0) to speed up
        with patch("buncker_fetch.registry_client.time.sleep"):
            manifest = client.fetch_manifest("library/test", "latest")
        assert manifest["schemaVersion"] == 2

    def test_all_retries_exhausted(self, mock_server, client):
        MockRegistryHandler.fail_count = 10  # always fail
        _patch_https_to_http(client)
        with (
            patch("buncker_fetch.registry_client.time.sleep"),
            pytest.raises(RegistryError, match="HTTP 500"),
        ):
            client.fetch_manifest("library/test", "latest")


class TestRateLimit:
    """Test 429 rate limiting with retry."""

    def test_429_retries_then_succeeds(self, mock_server, client):
        """429 on first attempt, success on second."""
        _patch_https_to_http(client)

        call_count = 0
        original_open = client._opener.open

        def counting_open(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate 429
                import urllib.error

                resp = urllib.error.HTTPError(
                    req.full_url, 429, "Too Many Requests", {"Retry-After": "1"}, None
                )
                raise resp
            return original_open(req, timeout=timeout)

        with (
            patch.object(client._opener, "open", side_effect=counting_open),
            patch("buncker_fetch.registry_client.time.sleep") as mock_sleep,
        ):
            manifest = client.fetch_manifest("library/test", "latest")

        assert manifest["schemaVersion"] == 2
        mock_sleep.assert_called_once_with(1.0)

    def test_429_exhausts_retries(self, mock_server, client):
        """429 on every attempt raises RegistryError."""
        MockRegistryHandler.rate_limit = True
        _patch_https_to_http(client)
        with (
            patch("buncker_fetch.registry_client.time.sleep"),
            pytest.raises(RegistryError, match="Rate limited"),
        ):
            client.fetch_manifest("library/test", "latest")

    def test_429_respects_retry_after_header(self, mock_server, client):
        """Retry-After header value is used as sleep duration."""
        _patch_https_to_http(client)

        call_count = 0
        original_open = client._opener.open

        def counting_open(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                import urllib.error

                resp = urllib.error.HTTPError(
                    req.full_url, 429, "Too Many Requests", {"Retry-After": "42"}, None
                )
                raise resp
            return original_open(req, timeout=timeout)

        with (
            patch.object(client._opener, "open", side_effect=counting_open),
            patch("buncker_fetch.registry_client.time.sleep") as mock_sleep,
        ):
            client.fetch_manifest("library/test", "latest")

        mock_sleep.assert_called_once_with(42.0)


class TestTimeout:
    """Test connection timeout handling."""

    def test_connection_timeout(self):
        # Use a non-routable address to trigger timeout
        client = RegistryClient("192.0.2.1:1")  # RFC 5737 TEST-NET
        with pytest.raises(RegistryError, match="Connection failed"):
            client.fetch_manifest("library/test", "latest")


class TestParseRetryAfter:
    """Test _parse_retry_after helper."""

    def test_integer_seconds(self):
        assert _parse_retry_after("30") == 30.0

    def test_empty_string_returns_default(self):
        assert _parse_retry_after("") == 5.0

    def test_minimum_is_one_second(self):
        assert _parse_retry_after("0") == 1.0

    def test_unparseable_returns_default(self):
        assert _parse_retry_after("not-a-number") == 5.0


class TestRateLimitHeaders:
    """Test _log_rate_limit_headers observability."""

    def test_logs_warning_when_remaining_low(self, caplog):
        class FakeResponse:
            headers = {
                "RateLimit-Remaining": "5",
                "RateLimit-Limit": "100",
                "RateLimit-Reset": "60",
            }

        import logging

        with caplog.at_level(logging.WARNING, logger="buncker.fetch.registry_client"):
            _log_rate_limit_headers(FakeResponse(), "docker.io")

        assert any("registry_rate_limit_low" in r.message for r in caplog.records)

    def test_no_warning_when_remaining_high(self, caplog):
        class FakeResponse:
            headers = {"RateLimit-Remaining": "50", "RateLimit-Limit": "100"}

        import logging

        with caplog.at_level(logging.WARNING, logger="buncker.fetch.registry_client"):
            _log_rate_limit_headers(FakeResponse(), "docker.io")

        assert not any("registry_rate_limit_low" in r.message for r in caplog.records)

    def test_no_headers_no_crash(self):
        class FakeResponse:
            headers = {}

        _log_rate_limit_headers(FakeResponse(), "docker.io")  # should not raise


class TestLoadCredentials:
    """Test credentials loading from env vars."""

    def test_loads_from_env(self):
        config = {
            "registries": {
                "docker.io": {
                    "user_env": "TEST_DOCKER_USER",
                    "pass_env": "TEST_DOCKER_PASS",
                }
            }
        }
        env = {
            "TEST_DOCKER_USER": "myuser",
            "TEST_DOCKER_PASS": "mypass",
        }
        with patch.dict("os.environ", env):
            creds = load_credentials(config, "docker.io")
        assert creds == {"username": "myuser", "password": "mypass"}

    def test_returns_none_when_not_configured(self):
        assert load_credentials({}, "docker.io") is None

    def test_returns_none_when_env_missing(self):
        config = {
            "registries": {
                "docker.io": {
                    "user_env": "NONEXISTENT_USER",
                    "pass_env": "NONEXISTENT_PASS",
                }
            }
        }
        assert load_credentials(config, "docker.io") is None
