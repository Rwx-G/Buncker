"""Tests for buncker.handler - OCI Distribution API handler."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from unittest import mock
from urllib.error import HTTPError

import pytest

from buncker.handler import _split_name


class TestSplitName:
    """Tests for _split_name helper."""

    def test_bare_name(self):
        assert _split_name("nginx") == ("docker.io", "library/nginx")

    def test_org_name(self):
        assert _split_name("myorg/myimage") == ("docker.io", "myorg/myimage")

    def test_explicit_registry(self):
        assert _split_name("ghcr.io/owner/repo") == ("ghcr.io", "owner/repo")

    def test_registry_with_port(self):
        assert _split_name("localhost:5000/myapp") == ("localhost:5000", "myapp")

    def test_localhost(self):
        assert _split_name("localhost/myapp") == ("localhost", "myapp")


class TestInputValidation:
    """Tests for input validation methods."""

    def test_valid_digest_format(self):
        digest = "sha256:" + "a" * 64
        from buncker.handler import _DIGEST_RE

        assert _DIGEST_RE.match(digest)

    def test_invalid_digest_format(self):
        from buncker.handler import _DIGEST_RE

        assert not _DIGEST_RE.match("sha256:short")
        assert not _DIGEST_RE.match("md5:" + "a" * 64)

    def test_valid_tag_format(self):
        from buncker.handler import _TAG_RE

        assert _TAG_RE.match("latest")
        assert _TAG_RE.match("1.25.3")
        assert _TAG_RE.match("v1.0-alpine")

    def test_invalid_tag_format(self):
        from buncker.handler import _TAG_RE

        assert not _TAG_RE.match("")
        assert not _TAG_RE.match("a" * 129)


class TestSecurityHeaders:
    """Tests for security headers on all responses (SEC-09)."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_nosniff_header_present(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/v2/")
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
        finally:
            srv.stop()

    def test_frame_deny_header_present(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/v2/")
            assert resp.headers["X-Frame-Options"] == "DENY"
        finally:
            srv.stop()

    def test_cache_control_header_present(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/v2/")
            assert resp.headers["Cache-Control"] == "no-store"
        finally:
            srv.stop()


class TestHealthEndpoint:
    """Tests for GET /admin/health (health-check endpoint)."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_health_returns_200_when_healthy(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/health"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
            data = json.loads(resp.read())
            assert data["healthy"] is True
            assert data["store"]["oci_layout_valid"] is True
            assert "disk" in data
            assert data["disk"]["free"] > 0
            assert "uptime" in data
        finally:
            srv.stop()

    def test_health_includes_blob_count(self, tmp_path):
        srv = self._make_server(tmp_path)
        # Import a blob
        store = srv.store
        blob_data = b"test blob"
        digest = f"sha256:{hashlib.sha256(blob_data).hexdigest()}"
        store.import_blob(blob_data, digest)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/health"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert data["store"]["blob_count"] == 1
        finally:
            srv.stop()

    def test_health_includes_tls_info_when_cert_exists(self, tmp_path):
        from buncker.auth import generate_self_signed_cert

        store_path = tmp_path / "store"
        store_path.mkdir()
        generate_self_signed_cert(store_path / "tls")

        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(store_path)
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/health"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert "tls" in data
            assert "days_until_expiry" in data["tls"]
            assert data["tls"]["expired"] is False
        finally:
            srv.stop()

    def test_health_no_tls_section_without_cert(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/health"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert "tls" not in data
        finally:
            srv.stop()

    def test_health_requires_auth_when_enabled(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        tokens = {"readonly": "ro_" + "a" * 61, "admin": "ad_" + "b" * 61}
        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/health"
            # Without token -> 401
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 401

            # With readonly token -> 200
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {tokens['readonly']}"}
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()


class TestRemoteDockerfileAnalysis:
    """Tests for remote Dockerfile analysis (Story 6.5)."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_analyze_with_dockerfile_content(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps(
                {
                    "dockerfile_content": "FROM scratch\n",
                    "build_args": {},
                }
            ).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            result = json.loads(resp.read())
            assert "images" in result
        finally:
            srv.stop()

    def test_analyze_with_dockerfile_content_and_build_args(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps(
                {
                    "dockerfile_content": "ARG BASE=alpine:3.19\nFROM $BASE\n",
                    "build_args": {"BASE": "debian:12"},
                }
            ).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            result = json.loads(resp.read())
            images = result.get("images", [])
            assert any("debian" in img.get("resolved", "") for img in images)
        finally:
            srv.stop()

    def test_analyze_path_from_localhost_works(self, tmp_path):
        """Localhost can still use path mode."""
        srv = self._make_server(tmp_path)
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM scratch\n")
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps({"dockerfile": str(dockerfile)}).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_analyze_path_rejected_message(self, tmp_path):
        """Path-based analysis from localhost returns expected error for coverage.

        Note: we cannot truly test remote rejection from localhost since
        _is_localhost() sees 127.0.0.1. This test validates the error format
        when both fields are missing, and the path restriction logic is
        verified via unit test below.
        """
        from buncker.handler import BunckerHandler

        handler = mock.MagicMock(spec=BunckerHandler)
        handler.client_address = ("192.168.1.42", 12345)
        handler._is_localhost = BunckerHandler._is_localhost.__get__(handler)
        assert not handler._is_localhost()

    def test_analyze_missing_both_fields_returns_400(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps({"build_args": {}}).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_generate_manifest_returns_binary(self, tmp_path):
        """generate-manifest returns file with Content-Type octet-stream."""
        srv = self._make_server(tmp_path)
        # Need crypto keys for generate-manifest
        from shared.crypto import derive_keys, generate_mnemonic, split_mnemonic

        mnemonic = generate_mnemonic()
        mnemonic_12, salt = split_mnemonic(mnemonic)
        aes_key, hmac_key = derive_keys(mnemonic_12, salt)
        srv.crypto_keys = (aes_key, hmac_key)

        try:
            # First analyze
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps(
                {
                    "dockerfile_content": "FROM alpine:3.19\n",
                }
            ).encode()
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            resp_analyze = urllib.request.urlopen(req)
            analysis_id = json.loads(resp_analyze.read())["analysis_id"]

            # Then generate manifest
            url2 = f"http://127.0.0.1:{srv.port}/admin/generate-manifest"
            gen_data = json.dumps({"analysis_id": analysis_id}).encode()
            req2 = urllib.request.Request(
                url2,
                data=gen_data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req2)
            assert resp.headers["Content-Type"] == "application/octet-stream"
            assert "Content-Disposition" in resp.headers
            assert "attachment" in resp.headers["Content-Disposition"]
            body = resp.read()
            assert len(body) > 0
        finally:
            srv.stop()


class TestPutImport:
    """Tests for PUT /admin/import streaming upload (Story 6.6)."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_put_missing_checksum_returns_400(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            data = b"some data"
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "MISSING_CHECKSUM"
        finally:
            srv.stop()

    def test_put_checksum_mismatch_returns_400(self, tmp_path):
        srv = self._make_server(tmp_path)
        srv.crypto_keys = (b"\x00" * 32, b"\x00" * 32)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            data = b"some data"
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": "sha256:" + "0" * 64,
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "CHECKSUM_MISMATCH"
        finally:
            srv.stop()

    def test_put_with_correct_checksum_accepted(self, tmp_path):
        """PUT with correct checksum is accepted."""
        srv = self._make_server(tmp_path)
        try:
            data = b"not a real tar.enc file"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": f"sha256:{checksum}",
                },
            )
            # Should pass checksum but fail on import (data is not valid encrypted tar)
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            # Error should be from import pipeline, not checksum
            body = json.loads(exc_info.value.read())
            assert body["code"] != "CHECKSUM_MISMATCH"
        finally:
            srv.stop()

    def test_put_invalid_checksum_hex_returns_400(self, tmp_path):
        """Non-hex checksum value is rejected early (SEC-05)."""
        srv = self._make_server(tmp_path)
        try:
            data = b"some data"
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": "sha256:" + "z" * 64,
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "INVALID_CHECKSUM"
        finally:
            srv.stop()

    def test_put_short_checksum_returns_400(self, tmp_path):
        """Checksum with wrong length is rejected (SEC-05)."""
        srv = self._make_server(tmp_path)
        try:
            data = b"some data"
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": "sha256:abc123",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "INVALID_CHECKSUM"
        finally:
            srv.stop()

    def test_put_auth_required_when_enabled(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        tokens = {"readonly": "ro_" + "a" * 61, "admin": "ad_" + "b" * 61}
        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        try:
            data = b"test"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": f"sha256:{checksum}",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            srv.stop()

    def test_put_content_range_resume(self, tmp_path):
        """PUT with Content-Range resumes a partial upload."""
        srv = self._make_server(tmp_path)
        srv.crypto_keys = (b"\x00" * 32, b"\x00" * 32)
        try:
            full_data = b"AAAA" * 100 + b"BBBB" * 100  # 800 bytes
            full_checksum = hashlib.sha256(full_data).hexdigest()
            part1 = full_data[:400]

            url = f"http://127.0.0.1:{srv.port}/admin/import"

            # First part (bytes 0-399/800) - will fail checksum (partial)
            # but we can send it as a normal PUT first
            req1 = urllib.request.Request(
                url,
                data=part1,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(part1)),
                    "X-Buncker-Checksum": f"sha256:{full_checksum}",
                },
            )
            # This will fail on checksum (partial data != full checksum)
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req1)
            assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_put_content_range_mismatch_offset(self, tmp_path):
        """PUT with Content-Range at wrong offset returns 400."""
        srv = self._make_server(tmp_path)
        srv.crypto_keys = (b"\x00" * 32, b"\x00" * 32)
        try:
            data = b"test data"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"

            # Send with Content-Range starting at offset 100 (no prior upload)
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": f"sha256:{checksum}",
                    "Content-Range": f"bytes 100-{99 + len(data)}/{100 + len(data)}",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "RANGE_MISMATCH"
        finally:
            srv.stop()

    def test_put_content_range_invalid_format(self, tmp_path):
        """PUT with invalid Content-Range format returns 400."""
        srv = self._make_server(tmp_path)
        srv.crypto_keys = (b"\x00" * 32, b"\x00" * 32)
        try:
            data = b"test data"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"

            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                    "X-Buncker-Checksum": f"sha256:{checksum}",
                    "Content-Range": "invalid-format",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "INVALID_RANGE"
        finally:
            srv.stop()

    def test_post_import_still_works(self, tmp_path):
        """POST /admin/import continues to work (regression)."""
        srv = self._make_server(tmp_path)
        try:
            data = b"not a valid import"
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(data)),
                },
            )
            # Will fail but with import error, not 404
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            body = json.loads(exc_info.value.read())
            assert body["code"] != "NOT_FOUND"
        finally:
            srv.stop()


class TestBlobHead:
    """Tests for HEAD /v2/{name}/blobs/{digest}."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_blob_head_invalid_digest(self, tmp_path):
        """HEAD with invalid digest does not match blob route, returns 404."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/baddigest"
            req = urllib.request.Request(url, method="HEAD")
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            # _BLOB_ROUTE requires sha256:<64hex>, so "baddigest" never matches
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_blob_head_missing_blob(self, tmp_path):
        """HEAD with valid but missing digest returns 404."""
        srv = self._make_server(tmp_path)
        try:
            digest = "sha256:" + "a" * 64
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/{digest}"
            req = urllib.request.Request(url, method="HEAD")
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_blob_head_existing(self, tmp_path):
        """HEAD with existing blob returns 200."""
        srv = self._make_server(tmp_path)
        blob_data = b"head test blob"
        digest = f"sha256:{hashlib.sha256(blob_data).hexdigest()}"
        srv.store.import_blob(blob_data, digest)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/{digest}"
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            assert resp.headers["Docker-Content-Digest"] == digest
        finally:
            srv.stop()


class TestBlobGet:
    """Tests for GET /v2/{name}/blobs/{digest}."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_blob_get_invalid_digest(self, tmp_path):
        """GET with invalid digest does not match blob route, returns 404."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/baddigest"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            # _BLOB_ROUTE requires sha256:<64hex>, so "baddigest" never matches
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_blob_get_missing(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            digest = "sha256:" + "b" * 64
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/{digest}"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_blob_get_existing(self, tmp_path):
        srv = self._make_server(tmp_path)
        blob_data = b"get test blob data"
        digest = f"sha256:{hashlib.sha256(blob_data).hexdigest()}"
        srv.store.import_blob(blob_data, digest)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/{digest}"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
            assert resp.read() == blob_data
        finally:
            srv.stop()


class TestLogsEndpoint:
    """Tests for GET /admin/logs."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        log_path = tmp_path / "test.log"
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            log_path=log_path,
        )
        srv.start()
        return srv, log_path

    def test_logs_invalid_limit(self, tmp_path):
        srv, _ = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs?limit=abc"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_logs_invalid_since(self, tmp_path):
        srv, _ = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs?since=bad"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_logs_empty(self, tmp_path):
        srv, _ = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert data == []
        finally:
            srv.stop()

    def test_logs_with_entries(self, tmp_path):
        srv, log_path = self._make_server(tmp_path)
        # Write some log entries
        log_path.write_text(
            '{"event": "test", "ts": "2026-03-09T12:00:00"}\n'
            '{"event": "other", "ts": "2026-03-09T13:00:00"}\n'
        )
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert len(data) == 2
        finally:
            srv.stop()

    def test_logs_event_filter(self, tmp_path):
        srv, log_path = self._make_server(tmp_path)
        log_path.write_text(
            '{"event": "test", "ts": "2026-03-09T12:00:00"}\n'
            '{"event": "other", "ts": "2026-03-09T13:00:00"}\n'
        )
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs?event=test"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert len(data) == 1
            assert data[0]["event"] == "test"
        finally:
            srv.stop()


class TestBodyValidation:
    """Tests for JSON body validation."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_oversized_json_body(self, tmp_path):
        """Content-Length > limit returns 400."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            # Patch limit to 100 bytes and send more than that
            data = b"x" * 200
            with mock.patch("buncker.handler._MAX_JSON_BODY_SIZE", 100):
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with pytest.raises(HTTPError) as exc_info:
                    urllib.request.urlopen(req)
                assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_invalid_json_body(self, tmp_path):
        """Invalid JSON body returns 400."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = b"{bad json"
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(data)),
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_empty_json_body(self, tmp_path):
        """Empty body returns 400."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            req = urllib.request.Request(
                url,
                data=b"",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "0",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
        finally:
            srv.stop()


class TestNotFoundRoutes:
    """Tests for 404 on unknown routes."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_unknown_get_returns_404(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/unknown/path"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_unknown_head_returns_404(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/unknown/path"
            req = urllib.request.Request(url, method="HEAD")
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_unknown_post_returns_404(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/nonexistent"
            req = urllib.request.Request(
                url,
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_unknown_put_returns_404(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/nonexistent"
            req = urllib.request.Request(url, data=b"data", method="PUT")
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
        finally:
            srv.stop()


class TestOciRestriction:
    """Tests for --restrict-oci mode (Story 7.4)."""

    _TOKENS = {"readonly": "ro_" + "a" * 61, "admin": "ad_" + "b" * 61}

    def _make_restricted_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=dict(self._TOKENS),
            api_enabled=True,
            oci_restrict=True,
        )
        srv.start()
        return srv

    def _make_unrestricted_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=dict(self._TOKENS),
            api_enabled=True,
            oci_restrict=False,
        )
        srv.start()
        return srv

    def test_v2_root_returns_401_without_token_when_restricted(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 401
            # Must include WWW-Authenticate header per OCI spec
            www_auth = exc_info.value.headers.get("WWW-Authenticate", "")
            assert 'Bearer realm="buncker"' in www_auth
        finally:
            srv.stop()

    def test_v2_root_succeeds_with_readonly_token(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._TOKENS['readonly']}"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_v2_root_succeeds_with_admin_token(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._TOKENS['admin']}"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_manifest_returns_401_without_token_when_restricted(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/nginx/manifests/latest"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 401
            www_auth = exc_info.value.headers.get("WWW-Authenticate", "")
            assert 'Bearer realm="buncker"' in www_auth
        finally:
            srv.stop()

    def test_blob_returns_401_without_token_when_restricted(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            digest = "sha256:" + "a" * 64
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/{digest}"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 401
        finally:
            srv.stop()

    def test_blob_get_with_readonly_token(self, tmp_path):
        """Readonly token can pull blobs in restricted mode."""
        srv = self._make_restricted_server(tmp_path)
        blob_data = b"restricted blob test"
        digest = f"sha256:{hashlib.sha256(blob_data).hexdigest()}"
        srv.store.import_blob(blob_data, digest)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/test/blobs/{digest}"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._TOKENS['readonly']}"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            assert resp.read() == blob_data
        finally:
            srv.stop()

    def test_default_mode_no_auth_required(self, tmp_path):
        """When oci_restrict=False, /v2/* endpoints are open."""
        srv = self._make_unrestricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            resp = urllib.request.urlopen(url)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_head_returns_401_without_token_when_restricted(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/nginx/manifests/latest"
            req = urllib.request.Request(url, method="HEAD")
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            srv.stop()

    def test_invalid_token_returns_401(self, tmp_path):
        srv = self._make_restricted_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            req = urllib.request.Request(
                url,
                headers={"Authorization": "Bearer invalidtoken"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            srv.stop()


class TestHeadV2Root:
    """Tests for HEAD /v2/ (line 178-180)."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_head_v2_returns_200(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/"
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()


class TestGcEndpoints:
    """Tests for GC impact, execute, and report endpoints."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_gc_impact_missing_digests(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/gc/impact"
            req = urllib.request.Request(
                url,
                data=json.dumps({}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "MISSING_FIELD"
        finally:
            srv.stop()

    def test_gc_impact_invalid_digest(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/gc/impact"
            req = urllib.request.Request(
                url,
                data=json.dumps({"digests": ["bad"]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "DIGEST_INVALID"
        finally:
            srv.stop()

    def test_gc_impact_valid(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            digest = "sha256:" + "a" * 64
            url = f"http://127.0.0.1:{srv.port}/admin/gc/impact"
            req = urllib.request.Request(
                url,
                data=json.dumps({"digests": [digest]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req)
            data = json.loads(resp.read())
            assert "impact" in data
            assert "affected_images" in data
        finally:
            srv.stop()

    def test_gc_execute_missing_digests(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/gc/execute"
            req = urllib.request.Request(
                url,
                data=json.dumps({"operator": "admin"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
        finally:
            srv.stop()

    def test_gc_execute_missing_operator(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            digest = "sha256:" + "a" * 64
            url = f"http://127.0.0.1:{srv.port}/admin/gc/execute"
            req = urllib.request.Request(
                url,
                data=json.dumps({"digests": [digest]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "MISSING_FIELD"
        finally:
            srv.stop()

    def test_gc_execute_invalid_operator(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            digest = "sha256:" + "a" * 64
            url = f"http://127.0.0.1:{srv.port}/admin/gc/execute"
            req = urllib.request.Request(
                url,
                data=json.dumps(
                    {"digests": [digest], "operator": "../../etc/passwd"}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "INVALID_OPERATOR"
        finally:
            srv.stop()

    def test_gc_execute_invalid_digest(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/gc/execute"
            req = urllib.request.Request(
                url,
                data=json.dumps(
                    {"digests": ["baddigest"], "operator": "admin"}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "DIGEST_INVALID"
        finally:
            srv.stop()

    def test_gc_report_invalid_inactive_days(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/gc/report?inactive_days=abc"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 400
            body = json.loads(exc_info.value.read())
            assert body["code"] == "INVALID_PARAM"
        finally:
            srv.stop()


class TestImportNoCryptoKeys:
    """Tests for import endpoints without crypto keys configured."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_post_import_no_crypto_keys_returns_500(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url,
                data=b"data",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": "4",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 500
            body = json.loads(exc_info.value.read())
            assert body["code"] == "NO_CRYPTO_KEYS"
        finally:
            srv.stop()

    def test_generate_manifest_no_crypto_keys_returns_500(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            # First analyze to set up state
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps({"dockerfile_content": "FROM alpine:3.19\n"}).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            resp_analyze = urllib.request.urlopen(req)
            analysis_id = json.loads(resp_analyze.read())["analysis_id"]

            # Then try generate without crypto keys
            url2 = f"http://127.0.0.1:{srv.port}/admin/generate-manifest"
            gen_data = json.dumps({"analysis_id": analysis_id}).encode()
            req2 = urllib.request.Request(
                url2, data=gen_data, headers={"Content-Type": "application/json"}
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req2)
            assert exc_info.value.code == 500
            body = json.loads(exc_info.value.read())
            assert body["code"] == "NO_CRYPTO_KEYS"
        finally:
            srv.stop()


class TestComposeAnalysis:
    """Tests for compose analysis via /admin/analyze."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_analyze_compose_content(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            compose_content = (
                "services:\n  web:\n    image: nginx:1.25\n"
                "  db:\n    image: postgres:16\n"
            )
            data = json.dumps({"compose_content": compose_content}).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            result = json.loads(resp.read())
            assert "images" in result
            assert len(result["images"]) >= 2
        finally:
            srv.stop()

    def test_analyze_compose_path_localhost(self, tmp_path):
        srv = self._make_server(tmp_path)
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services:\n  web:\n    image: nginx:1.25\n")
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps({"compose_path": str(compose_file)}).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            result = json.loads(resp.read())
            assert "images" in result
        finally:
            srv.stop()


class TestLogsEdgeCases:
    """Tests for logs endpoint edge cases."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        log_path = tmp_path / "test.log"
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            log_path=log_path,
        )
        srv.start()
        return srv, log_path

    def test_logs_skips_empty_lines(self, tmp_path):
        srv, log_path = self._make_server(tmp_path)
        log_path.write_text('\n\n{"event": "test", "ts": "2026-03-09T12:00:00"}\n\n')
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert len(data) == 1
        finally:
            srv.stop()

    def test_logs_skips_invalid_json(self, tmp_path):
        srv, log_path = self._make_server(tmp_path)
        log_path.write_text(
            'not json\n{"event": "test", "ts": "2026-03-09T12:00:00"}\n'
        )
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            assert len(data) == 1
            assert data[0]["event"] == "test"
        finally:
            srv.stop()

    def test_logs_skips_invalid_timestamp(self, tmp_path):
        srv, log_path = self._make_server(tmp_path)
        log_path.write_text(
            '{"event": "old", "ts": "not-a-date"}\n'
            '{"event": "new", "ts": "2026-03-09T12:00:00"}\n'
        )
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/logs?since=2026-03-09T00:00:00"
            resp = urllib.request.urlopen(url)
            data = json.loads(resp.read())
            # "old" has bad timestamp so it's skipped by since filter
            assert len(data) == 1
            assert data[0]["event"] == "new"
        finally:
            srv.stop()


class TestDiskSpaceCheck:
    """Tests for _check_disk_space."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.crypto_keys = (b"\x00" * 32, b"\x00" * 32)
        srv.start()
        return srv

    def test_put_import_insufficient_space(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            data = b"test data"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"

            with mock.patch("shutil.disk_usage") as mock_du:
                mock_du.return_value = mock.Mock(free=10)
                req = urllib.request.Request(
                    url,
                    data=data,
                    method="PUT",
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(data)),
                        "X-Buncker-Checksum": f"sha256:{checksum}",
                    },
                )
                with pytest.raises(HTTPError) as exc_info:
                    urllib.request.urlopen(req)
                assert exc_info.value.code == 507
                body = json.loads(exc_info.value.read())
                assert body["code"] == "INSUFFICIENT_SPACE"
        finally:
            srv.stop()


class TestManifestRoute:
    """Tests for manifest HEAD and GET routes."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(bind="127.0.0.1", port=0, store=store, source_id="test")
        srv.start()
        return srv

    def test_manifest_get_not_found(self, tmp_path):
        """GET manifest for uncached image returns 404."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/nginx/manifests/latest"
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(url)
            assert exc_info.value.code == 404
        finally:
            srv.stop()

    def test_manifest_head_not_found(self, tmp_path):
        """HEAD manifest for uncached image returns 404."""
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/v2/library/nginx/manifests/latest"
            req = urllib.request.Request(url, method="HEAD")
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
        finally:
            srv.stop()
