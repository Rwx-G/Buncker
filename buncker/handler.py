"""OCI Distribution API request handler for Buncker."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from buncker.store import Store
from shared.exceptions import StoreError

_log = logging.getLogger("buncker.handler")

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_TAG_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_CHUNK_SIZE = 65536

# OCI route patterns
_V2_ROOT = re.compile(r"^/v2/?$")
_MANIFEST_ROUTE = re.compile(r"^/v2/(.+)/manifests/(.+)$")
_BLOB_ROUTE = re.compile(r"^/v2/(.+)/blobs/(sha256:[a-f0-9]{64})$")


class BunckerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OCI Distribution API endpoints."""

    def __init__(self, *args, server_ref=None, **kwargs):
        self._server_ref = server_ref
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Override to use structured logging instead of stderr."""
        _log.debug("http_request", extra={"message": format % args})

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self):
        """Route GET requests."""
        if _V2_ROOT.match(self.path):
            self._handle_v2_root()
            return

        m = _MANIFEST_ROUTE.match(self.path)
        if m:
            self._handle_manifest_get(m.group(1), m.group(2))
            return

        m = _BLOB_ROUTE.match(self.path)
        if m:
            self._handle_blob_get(m.group(1), m.group(2))
            return

        self._send_not_found()

    # ------------------------------------------------------------------
    # HEAD
    # ------------------------------------------------------------------

    def do_HEAD(self):
        """Route HEAD requests."""
        if _V2_ROOT.match(self.path):
            self._handle_v2_root()
            return

        m = _MANIFEST_ROUTE.match(self.path)
        if m:
            self._handle_manifest_head(m.group(1), m.group(2))
            return

        m = _BLOB_ROUTE.match(self.path)
        if m:
            self._handle_blob_head(m.group(1), m.group(2))
            return

        self._send_not_found()

    # ------------------------------------------------------------------
    # OCI Distribution endpoints
    # ------------------------------------------------------------------

    def _handle_v2_root(self):
        """GET /v2/ - Version check."""
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Docker-Distribution-API-Version", "registry/2.0")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle_manifest_get(self, name: str, reference: str):
        """GET /v2/{name}/manifests/{reference}."""
        if not self._validate_name(name):
            return
        if not self._validate_reference(reference):
            return

        store = self._get_store()
        manifest = self._lookup_manifest(store, name, reference)
        if manifest is None:
            self._send_oci_error(404, "MANIFEST_UNKNOWN", "manifest unknown to registry")
            return

        body = json.dumps(
            {k: v for k, v in manifest.items() if k != "_buncker"},
            sort_keys=True,
        ).encode()
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        media_type = manifest.get(
            "mediaType",
            "application/vnd.oci.image.manifest.v1+json",
        )

        self.send_response(200)
        self.send_header("Docker-Content-Digest", digest)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Docker-Distribution-API-Version", "registry/2.0")
        self.end_headers()
        self.wfile.write(body)

    def _handle_manifest_head(self, name: str, reference: str):
        """HEAD /v2/{name}/manifests/{reference}."""
        if not self._validate_name(name):
            return
        if not self._validate_reference(reference):
            return

        store = self._get_store()
        manifest = self._lookup_manifest(store, name, reference)
        if manifest is None:
            self._send_oci_error(404, "MANIFEST_UNKNOWN", "manifest unknown to registry")
            return

        body = json.dumps(
            {k: v for k, v in manifest.items() if k != "_buncker"},
            sort_keys=True,
        ).encode()
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        media_type = manifest.get(
            "mediaType",
            "application/vnd.oci.image.manifest.v1+json",
        )

        self.send_response(200)
        self.send_header("Docker-Content-Digest", digest)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Docker-Distribution-API-Version", "registry/2.0")
        self.end_headers()

    def _handle_blob_get(self, name: str, digest: str):
        """GET /v2/{name}/blobs/{digest} - stream blob."""
        if not self._validate_name(name):
            return
        if not _DIGEST_RE.match(digest):
            self._send_oci_error(400, "DIGEST_INVALID", "invalid digest format")
            return

        store = self._get_store()
        if not store.has_blob(digest):
            self._send_oci_error(404, "BLOB_UNKNOWN", "blob unknown to registry")
            return

        blob_path = store.get_blob(digest)
        size = blob_path.stat().st_size

        self.send_response(200)
        self.send_header("Docker-Content-Digest", digest)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()

        with open(blob_path, "rb") as f:
            while chunk := f.read(_CHUNK_SIZE):
                self.wfile.write(chunk)

        try:
            store.update_metadata(digest, "pull")
        except StoreError:
            _log.warning("metadata_update_failed", extra={"digest": digest})

    def _handle_blob_head(self, name: str, digest: str):
        """HEAD /v2/{name}/blobs/{digest}."""
        if not self._validate_name(name):
            return
        if not _DIGEST_RE.match(digest):
            self._send_oci_error(400, "DIGEST_INVALID", "invalid digest format")
            return

        store = self._get_store()
        if not store.has_blob(digest):
            self._send_oci_error(404, "BLOB_UNKNOWN", "blob unknown to registry")
            return

        blob_path = store.get_blob(digest)
        size = blob_path.stat().st_size

        self.send_response(200)
        self.send_header("Docker-Content-Digest", digest)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.end_headers()

    # ------------------------------------------------------------------
    # Manifest lookup
    # ------------------------------------------------------------------

    def _lookup_manifest(self, store: Store, name: str, reference: str) -> dict | None:
        """Look up a manifest in the cache by name and reference.

        Searches all platforms for the given registry/repository/reference.
        """
        from buncker.registry_client import ManifestCache

        cache = ManifestCache(store.path)
        registry, repository = _split_name(name)

        # Try common platforms
        for platform in ("linux/amd64", "linux/arm64", "linux/arm/v7"):
            manifest = cache.get_manifest(registry, repository, reference, platform)
            if manifest is not None:
                return manifest

        return None

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def _validate_name(self, name: str) -> bool:
        """Validate {name} parameter - prevent path traversal."""
        if ".." in name or name.startswith("/"):
            self._send_oci_error(400, "NAME_INVALID", "invalid repository name")
            return False
        return True

    def _validate_reference(self, reference: str) -> bool:
        """Validate {reference} - must be a valid tag or digest."""
        if reference.startswith("sha256:"):
            if not _DIGEST_RE.match(reference):
                self._send_oci_error(400, "DIGEST_INVALID", "invalid digest format")
                return False
        elif not _TAG_RE.match(reference):
            self._send_oci_error(400, "TAG_INVALID", "invalid tag format")
            return False
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_store(self) -> Store:
        """Get the store instance from the server."""
        return self._server_ref.store

    def _send_not_found(self):
        """Send a generic 404 response."""
        self._send_oci_error(404, "NOT_FOUND", "resource not found")

    def _send_oci_error(self, status: int, code: str, message: str):
        """Send a standard OCI error response."""
        body = json.dumps(
            {"errors": [{"code": code, "message": message}]}
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def _split_name(name: str) -> tuple[str, str]:
    """Split a {name} into (registry, repository).

    Docker convention: if the first segment has a dot or colon, it's a registry.
    Otherwise, it's a Docker Hub repository.
    """
    parts = name.split("/", 1)
    if len(parts) == 1:
        return "docker.io", f"library/{name}"

    first = parts[0]
    if "." in first or ":" in first or first == "localhost":
        return first, parts[1]

    return "docker.io", name
