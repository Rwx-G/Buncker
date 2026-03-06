"""Tests for buncker.handler - OCI Distribution API handler."""

from __future__ import annotations

import hashlib
import json
import urllib.request
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


class TestRemoteDockerfileAnalysis:
    """Tests for remote Dockerfile analysis (Story 6.5)."""

    def _make_server(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1", port=0, store=store, source_id="test"
        )
        srv.start()
        return srv

    def test_analyze_with_dockerfile_content(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps({
                "dockerfile_content": "FROM scratch\n",
                "build_args": {},
            }).encode()
            req = urllib.request.Request(
                url, data=data,
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
            data = json.dumps({
                "dockerfile_content": "ARG BASE=alpine:3.19\nFROM $BASE\n",
                "build_args": {"BASE": "debian:12"},
            }).encode()
            req = urllib.request.Request(
                url, data=data,
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
                url, data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            srv.stop()

    def test_analyze_missing_both_fields_returns_400(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/analyze"
            data = json.dumps({"build_args": {}}).encode()
            req = urllib.request.Request(
                url, data=data,
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
            data = json.dumps({
                "dockerfile_content": "FROM alpine:3.19\n",
            }).encode()
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req)

            # Then generate manifest
            url2 = f"http://127.0.0.1:{srv.port}/admin/generate-manifest"
            req2 = urllib.request.Request(
                url2, data=b"{}",
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
        srv = BunckerServer(
            bind="127.0.0.1", port=0, store=store, source_id="test"
        )
        srv.start()
        return srv

    def test_put_missing_checksum_returns_400(self, tmp_path):
        srv = self._make_server(tmp_path)
        try:
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            data = b"some data"
            req = urllib.request.Request(
                url, data=data, method="PUT",
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
                url, data=data, method="PUT",
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
        """PUT with correct checksum is accepted (even if import fails due to invalid data)."""
        srv = self._make_server(tmp_path)
        try:
            data = b"not a real tar.enc file"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url, data=data, method="PUT",
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

    def test_put_auth_required_when_enabled(self, tmp_path):
        from buncker.server import BunckerServer
        from buncker.store import Store

        tokens = {"readonly": "ro_" + "a" * 61, "admin": "ad_" + "b" * 61}
        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1", port=0, store=store,
            source_id="test", api_tokens=tokens, api_enabled=True,
        )
        srv.start()
        try:
            data = b"test"
            checksum = hashlib.sha256(data).hexdigest()
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url, data=data, method="PUT",
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

    def test_post_import_still_works(self, tmp_path):
        """POST /admin/import continues to work (regression)."""
        srv = self._make_server(tmp_path)
        try:
            data = b"not a valid import"
            url = f"http://127.0.0.1:{srv.port}/admin/import"
            req = urllib.request.Request(
                url, data=data,
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
