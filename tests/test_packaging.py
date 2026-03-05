"""Tests for .deb packaging structure and metadata."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST = PROJECT_ROOT / "dist"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)


@pytest.fixture(scope="module")
def built_debs():
    """Build .deb packages once for all tests in this module."""
    _run(["make", "build-deb"], cwd=PROJECT_ROOT)
    buncker = list(DIST.glob("buncker_*_all.deb"))
    fetch = list(DIST.glob("buncker-fetch_*_all.deb"))
    assert buncker, "buncker .deb not found in dist/"
    assert fetch, "buncker-fetch .deb not found in dist/"
    return {"buncker": buncker[0], "buncker-fetch": fetch[0]}


def _deb_info(deb_path: Path) -> str:
    return _run(["dpkg-deb", "--info", str(deb_path)]).stdout


def _deb_contents(deb_path: Path) -> str:
    return _run(["dpkg-deb", "--contents", str(deb_path)]).stdout


def _deb_field(deb_path: Path, field: str) -> str:
    return _run(["dpkg-deb", "--field", str(deb_path), field]).stdout.strip()


# --- buncker.deb ---


class TestBunckerDeb:
    def test_build_succeeds(self, built_debs):
        assert built_debs["buncker"].exists()

    def test_package_name(self, built_debs):
        assert _deb_field(built_debs["buncker"], "Package") == "buncker"

    def test_depends(self, built_debs):
        deps = _deb_field(built_debs["buncker"], "Depends")
        assert "python3" in deps
        assert "python3-cryptography" in deps

    def test_architecture(self, built_debs):
        assert _deb_field(built_debs["buncker"], "Architecture") == "all"

    def test_contains_entry_point(self, built_debs):
        contents = _deb_contents(built_debs["buncker"])
        assert "./usr/bin/buncker" in contents

    def test_contains_python_package(self, built_debs):
        contents = _deb_contents(built_debs["buncker"])
        assert "./usr/lib/buncker/buncker/__main__.py" in contents
        assert "./usr/lib/buncker/buncker/__init__.py" in contents

    def test_contains_shared_modules(self, built_debs):
        contents = _deb_contents(built_debs["buncker"])
        assert "./usr/lib/buncker/shared/crypto.py" in contents
        assert "./usr/lib/buncker/shared/oci.py" in contents

    def test_no_skeleton_config(self, built_debs):
        """Config is created by buncker setup, not shipped in the .deb."""
        contents = _deb_contents(built_debs["buncker"])
        assert "./etc/buncker/config.json" not in contents

    def test_contains_systemd_service(self, built_debs):
        contents = _deb_contents(built_debs["buncker"])
        assert "./lib/systemd/system/buncker.service" in contents

    def test_has_postinst(self, built_debs):
        info = _deb_info(built_debs["buncker"])
        assert "postinst" in info or "post-installation" in info.lower()


# --- buncker-fetch.deb ---


class TestBunckerFetchDeb:
    def test_build_succeeds(self, built_debs):
        assert built_debs["buncker-fetch"].exists()

    def test_package_name(self, built_debs):
        assert _deb_field(built_debs["buncker-fetch"], "Package") == "buncker-fetch"

    def test_depends(self, built_debs):
        deps = _deb_field(built_debs["buncker-fetch"], "Depends")
        assert "python3" in deps
        assert "python3-cryptography" in deps

    def test_contains_entry_point(self, built_debs):
        contents = _deb_contents(built_debs["buncker-fetch"])
        assert "./usr/bin/buncker-fetch" in contents

    def test_contains_python_package(self, built_debs):
        contents = _deb_contents(built_debs["buncker-fetch"])
        assert "./usr/lib/buncker-fetch/buncker_fetch/__main__.py" in contents
        assert "./usr/lib/buncker-fetch/buncker_fetch/__init__.py" in contents

    def test_contains_shared_modules(self, built_debs):
        contents = _deb_contents(built_debs["buncker-fetch"])
        assert "./usr/lib/buncker-fetch/shared/crypto.py" in contents
        assert "./usr/lib/buncker-fetch/shared/oci.py" in contents

    def test_no_config(self, built_debs):
        """buncker-fetch has no system config file."""
        contents = _deb_contents(built_debs["buncker-fetch"])
        assert "/etc/" not in contents

    def test_no_systemd_service(self, built_debs):
        """buncker-fetch is a CLI tool, no daemon."""
        contents = _deb_contents(built_debs["buncker-fetch"])
        assert "systemd" not in contents
