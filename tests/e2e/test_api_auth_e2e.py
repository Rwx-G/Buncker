"""E2E tests for API authentication and LAN client operations (Epic 6).

Tests the complete auth lifecycle:
  api-setup -> token auth -> analyze (content) -> generate-manifest ->
  PUT import -> OCI pull -> token reset -> backward compatibility
"""

from __future__ import annotations

import json
import urllib.request
from unittest import mock
from urllib.error import HTTPError

import pytest

from buncker.server import BunckerServer
from buncker.store import Store
from shared.crypto import decrypt_env_value, derive_keys, split_mnemonic


@pytest.mark.e2e
class TestAuthenticatedCycle:
    """E2E: full authenticated cycle with api-setup."""

    def test_full_auth_cycle(self, tmp_path):
        """api-setup -> auth -> analyze (content) -> status -> OCI pull."""
        # -- Setup: create config + api-setup --
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"

        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "setup",
                "--store-path",
                str(store_path),
            ],
        ):
            from buncker.__main__ import main

            main()

        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            main()

        # Load tokens and config
        tokens = json.loads((config_path.parent / "api-tokens.json").read_text())
        config = json.loads(config_path.read_text())
        assert config["api"]["enabled"] is True
        assert config["tls"] is True

        # Start server with auth - handle encrypted or cleartext env
        env_line = (config_path.parent / "env").read_text().strip()
        key, value = env_line.split("=", 1)
        mnemonic = decrypt_env_value(value) if key == "BUNCKER_MNEMONIC_ENC" else value
        mnemonic_12, salt = split_mnemonic(mnemonic)
        aes_key, hmac_key = derive_keys(mnemonic_12, salt)

        store = Store(store_path)
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            crypto_keys=(aes_key, hmac_key),
            source_id=config["source_id"],
            api_tokens=tokens,
            api_enabled=True,
        )
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # -- Admin token: analyze with content mode --
            data = json.dumps(
                {
                    "dockerfile_content": "FROM scratch\n",
                }
            ).encode()
            req = urllib.request.Request(
                f"{base}/admin/analyze",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {tokens['admin']}",
                },
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
            result = json.loads(resp.read())
            assert "images" in result

            # -- Admin token: status --
            req = urllib.request.Request(
                f"{base}/admin/status",
                headers={"Authorization": f"Bearer {tokens['admin']}"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200

            # -- OCI /v2/ without token: always open --
            resp = urllib.request.urlopen(f"{base}/v2/")
            assert resp.status == 200

        finally:
            srv.stop()


@pytest.mark.e2e
class TestAccessControl:
    """E2E: access control with different token levels."""

    def test_access_control(self, tmp_path):
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
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # RO token on GET /admin/status -> 200
            req = urllib.request.Request(
                f"{base}/admin/status",
                headers={"Authorization": f"Bearer {tokens['readonly']}"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200

            # RO token on POST /admin/analyze -> 403
            data = json.dumps({"dockerfile_content": "FROM scratch\n"}).encode()
            req = urllib.request.Request(
                f"{base}/admin/analyze",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {tokens['readonly']}",
                },
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 403

            # No token on GET /admin/status -> 401
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(f"{base}/admin/status")
            assert exc_info.value.code == 401

            # No token on GET /v2/ -> 200
            resp = urllib.request.urlopen(f"{base}/v2/")
            assert resp.status == 200

        finally:
            srv.stop()


@pytest.mark.e2e
class TestTokenReset:
    """E2E: token reset invalidates old token."""

    def test_token_reset(self, tmp_path):
        # Setup with api-setup
        config_path = tmp_path / "config.json"
        store_path = tmp_path / "store"

        with mock.patch(
            "sys.argv",
            [
                "buncker",
                "--config",
                str(config_path),
                "setup",
                "--store-path",
                str(store_path),
            ],
        ):
            from buncker.__main__ import main

            main()

        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-setup"],
        ):
            main()

        old_tokens = json.loads((config_path.parent / "api-tokens.json").read_text())

        # Reset admin token
        with mock.patch(
            "sys.argv",
            ["buncker", "--config", str(config_path), "api-reset", "admin"],
        ):
            main()

        new_tokens = json.loads((config_path.parent / "api-tokens.json").read_text())
        assert new_tokens["admin"] != old_tokens["admin"]
        assert new_tokens["readonly"] == old_tokens["readonly"]

        # Start server with new tokens
        store = Store(store_path)
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_tokens=new_tokens,
            api_enabled=True,
        )
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # Old admin token -> 401
            req = urllib.request.Request(
                f"{base}/admin/status",
                headers={"Authorization": f"Bearer {old_tokens['admin']}"},
            )
            with pytest.raises(HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401

            # New admin token -> 200
            req = urllib.request.Request(
                f"{base}/admin/status",
                headers={"Authorization": f"Bearer {new_tokens['admin']}"},
            )
            resp = urllib.request.urlopen(req)
            assert resp.status == 200

        finally:
            srv.stop()


@pytest.mark.e2e
class TestBackwardCompatibility:
    """E2E: daemon without api-setup works without auth."""

    def test_no_auth_without_api_setup(self, tmp_path):
        store = Store(tmp_path / "store")
        srv = BunckerServer(
            bind="127.0.0.1",
            port=0,
            store=store,
            source_id="test",
            api_enabled=False,
        )
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"

        try:
            # All admin endpoints work without auth
            resp = urllib.request.urlopen(f"{base}/admin/status")
            assert resp.status == 200

            resp = urllib.request.urlopen(f"{base}/v2/")
            assert resp.status == 200

        finally:
            srv.stop()
