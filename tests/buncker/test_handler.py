"""Tests for buncker.handler - OCI Distribution API handler."""

from __future__ import annotations

import hashlib
import json

import pytest

from buncker.handler import BunckerHandler, _split_name


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
