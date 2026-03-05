"""OCI Distribution API + Admin API request handler for Buncker."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from buncker import __version__
from buncker.store import Store
from shared.exceptions import ResolverError, StoreError, TransferError

_MAX_IMPORT_SIZE = 4 * 1024 * 1024 * 1024  # 4 GiB
_MAX_JSON_BODY_SIZE = 10 * 1024 * 1024  # 10 MiB

_log = logging.getLogger("buncker.handler")

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_TAG_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
_OPERATOR_RE = re.compile(r"^[a-zA-Z0-9._@-]{1,128}$")
_CHUNK_SIZE = 65536

# OCI route patterns
_V2_ROOT = re.compile(r"^/v2/?$")
_MANIFEST_ROUTE = re.compile(r"^/v2/(.+)/manifests/(.+)$")
# Blob route requires full sha256 digest in the URL; greedy (.+) for name is
# disambiguated by the fixed /blobs/ segment and the strict digest suffix.
_BLOB_ROUTE = re.compile(r"^/v2/(.+)/blobs/(sha256:[a-f0-9]{64})$")


class BunckerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OCI Distribution API + Admin API endpoints."""

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
        path = urlparse(self.path).path

        if _V2_ROOT.match(path):
            self._handle_v2_root()
            return

        # Admin GET routes
        if path == "/admin/status":
            self._handle_admin_status()
            return
        if path == "/admin/gc/report":
            self._handle_admin_gc_report()
            return
        if path == "/admin/logs":
            self._handle_admin_logs()
            return

        m = _MANIFEST_ROUTE.match(path)
        if m:
            self._handle_manifest_get(m.group(1), m.group(2))
            return

        m = _BLOB_ROUTE.match(path)
        if m:
            self._handle_blob_get(m.group(1), m.group(2))
            return

        self._send_not_found()

    # ------------------------------------------------------------------
    # HEAD
    # ------------------------------------------------------------------

    def do_HEAD(self):
        """Route HEAD requests."""
        path = urlparse(self.path).path

        if _V2_ROOT.match(path):
            self._handle_v2_root()
            return

        m = _MANIFEST_ROUTE.match(path)
        if m:
            self._handle_manifest_head(m.group(1), m.group(2))
            return

        m = _BLOB_ROUTE.match(path)
        if m:
            self._handle_blob_head(m.group(1), m.group(2))
            return

        self._send_not_found()

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------

    def do_POST(self):
        """Route POST requests."""
        path = urlparse(self.path).path

        if path == "/admin/analyze":
            self._handle_admin_analyze()
            return
        if path == "/admin/generate-manifest":
            self._handle_admin_generate_manifest()
            return
        if path == "/admin/import":
            self._handle_admin_import()
            return
        if path == "/admin/gc/execute":
            self._handle_admin_gc_execute()
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
        self._handle_manifest(name, reference, include_body=True)

    def _handle_manifest_head(self, name: str, reference: str):
        """HEAD /v2/{name}/manifests/{reference}."""
        self._handle_manifest(name, reference, include_body=False)

    def _handle_manifest(self, name: str, reference: str, *, include_body: bool):
        """Shared manifest GET/HEAD logic."""
        if not self._validate_name(name):
            return
        if not self._validate_reference(reference):
            return

        store = self._get_store()
        manifest = self._lookup_manifest(store, name, reference)
        if manifest is None:
            self._send_oci_error(
                404, "MANIFEST_UNKNOWN", "manifest unknown to registry"
            )
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
        if include_body:
            self.wfile.write(body)

    def _handle_blob_get(self, name: str, digest: str):
        """GET /v2/{name}/blobs/{digest} - stream blob with SHA256 verification."""
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

        # Stream blob while computing SHA256 to verify integrity
        h = hashlib.sha256()
        with open(blob_path, "rb") as f:
            while chunk := f.read(_CHUNK_SIZE):
                h.update(chunk)
                self.wfile.write(chunk)

        actual_digest = f"sha256:{h.hexdigest()}"
        if actual_digest != digest:
            _log.error(
                "blob_integrity_error",
                extra={"expected": digest, "actual": actual_digest},
            )

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
    # Admin API endpoints
    # ------------------------------------------------------------------

    def _handle_admin_analyze(self):
        """POST /admin/analyze - Analyze a Dockerfile."""
        body = self._read_json_body()
        if body is None:
            return

        dockerfile = body.get("dockerfile")
        if not dockerfile:
            self._send_admin_error(400, "MISSING_FIELD", "dockerfile field required")
            return

        # Path traversal prevention - reject paths with .. components
        if ".." in Path(dockerfile).parts:
            self._send_admin_error(400, "INVALID_PATH", "path traversal not allowed")
            return

        build_args = body.get("build_args", {})
        store = self._get_store()

        try:
            from buncker.registry_client import ManifestCache
            from buncker.resolver import resolve_dockerfile

            result = resolve_dockerfile(
                Path(dockerfile),
                build_args,
                store=store,
                registry_client=ManifestCache(store.path),
            )
        except ResolverError as e:
            self._send_admin_error(400, "RESOLVER_ERROR", str(e))
            return
        except Exception as e:
            self._send_admin_error(500, "INTERNAL_ERROR", str(e))
            return

        # Store analysis result for generate-manifest
        self._server_ref._last_analysis = result

        report = {
            "source_path": result.source_path,
            "images": [
                {
                    "raw": img.raw,
                    "resolved": img.resolved,
                    "registry": img.registry,
                    "repository": img.repository,
                    "tag": img.tag,
                    "digest": img.digest,
                    "platform": img.platform,
                    "is_internal": img.is_internal,
                    "is_private": img.is_private,
                }
                for img in result.images
            ],
            "present_blobs": list(result.present_blobs),
            "missing_blobs": result.missing_blobs,
            "total_missing_size": result.total_missing_size,
            "warnings": result.warnings,
        }

        self._send_json(200, report)

    def _handle_admin_generate_manifest(self):
        """POST /admin/generate-manifest - Generate encrypted transfer request."""
        analysis = getattr(self._server_ref, "_last_analysis", None)
        if analysis is None:
            self._send_admin_error(
                409, "NO_ANALYSIS", "no analysis pending - run /admin/analyze first"
            )
            return

        if not analysis.missing_blobs:
            self._send_admin_error(409, "NO_MISSING", "no missing blobs to request")
            return

        crypto_keys = getattr(self._server_ref, "crypto_keys", None)
        if crypto_keys is None:
            self._send_admin_error(500, "NO_CRYPTO_KEYS", "crypto keys not configured")
            return

        from shared.crypto import encrypt, sign

        aes_key, hmac_key = crypto_keys
        source_id = getattr(self._server_ref, "source_id", "buncker")

        # Collect unique external images for manifest fetching on online side
        images = []
        seen_images: set[str] = set()
        for img in analysis.images:
            if img.is_internal or img.is_private:
                continue
            key = f"{img.registry}/{img.repository}:{img.tag}"
            if key in seen_images:
                continue
            seen_images.add(key)
            images.append(
                {
                    "registry": img.registry,
                    "repository": img.repository,
                    "tag": img.tag,
                    "platform": img.platform or "linux/amd64",
                }
            )

        request_data = {
            "version": "1",
            "buncker_version": __version__,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "source_id": source_id,
            "images": images,
            "blobs": analysis.missing_blobs,
        }

        json_bytes = json.dumps(request_data).encode()
        signature = sign(json_bytes, hmac_key)
        signed_data = json_bytes + b"\n" + signature.encode()
        encrypted = encrypt(signed_data, aes_key)

        now = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        filename = f"buncker-request-{now}-{source_id}.json.enc"

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(encrypted)))
        self.end_headers()
        self.wfile.write(encrypted)

        # Clear analysis after generation
        self._server_ref._last_analysis = None

    def _handle_admin_import(self):
        """POST /admin/import - Import encrypted transfer response."""
        crypto_keys = getattr(self._server_ref, "crypto_keys", None)
        if crypto_keys is None:
            self._send_admin_error(500, "NO_CRYPTO_KEYS", "crypto keys not configured")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_admin_error(400, "EMPTY_BODY", "request body required")
            return

        if content_length > _MAX_IMPORT_SIZE:
            self._send_admin_error(
                400, "BODY_TOO_LARGE", "request body exceeds 4 GiB limit"
            )
            return

        # Read the raw body (encrypted .tar.enc file)
        raw_data = self.rfile.read(content_length)

        # Write to temp file and import
        import tempfile

        aes_key, hmac_key = crypto_keys
        store = self._get_store()

        with tempfile.NamedTemporaryFile(suffix=".tar.enc", delete=False) as tmp:
            tmp.write(raw_data)
            tmp_path = Path(tmp.name)

        try:
            from buncker.registry_client import ManifestCache
            from buncker.transfer import import_response

            result = import_response(
                tmp_path,
                aes_key=aes_key,
                hmac_key=hmac_key,
                store=store,
                manifest_cache=ManifestCache(store.path),
            )
            self._send_json(200, result)
        except TransferError as e:
            self._send_admin_error(400, "TRANSFER_ERROR", str(e))
        except Exception as e:
            self._send_admin_error(500, "INTERNAL_ERROR", str(e))
        finally:
            tmp_path.unlink(missing_ok=True)

    def _handle_admin_status(self):
        """GET /admin/status - System status."""
        store = self._get_store()
        blobs_dir = store.path / "blobs" / "sha256"

        blob_count = 0
        total_size = 0
        if blobs_dir.exists():
            for f in blobs_dir.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    blob_count += 1
                    total_size += f.stat().st_size

        source_id = getattr(self._server_ref, "source_id", "")
        start_time = getattr(self._server_ref, "_start_time", None)
        uptime = 0
        if start_time is not None:
            uptime = int(time.time() - start_time)

        status = {
            "version": __version__,
            "source_id": source_id,
            "store_path": str(store.path),
            "blob_count": blob_count,
            "total_size": total_size,
            "uptime": uptime,
        }
        self._send_json(200, status)

    def _handle_admin_gc_report(self):
        """GET /admin/gc/report - GC candidates."""
        params = parse_qs(urlparse(self.path).query)
        inactive_days_str = params.get("inactive_days", ["90"])[0]

        try:
            inactive_days = int(inactive_days_str)
        except ValueError:
            self._send_admin_error(
                400, "INVALID_PARAM", "inactive_days must be an integer"
            )
            return

        store = self._get_store()
        candidates = store.gc_report(inactive_days)
        self._send_json(200, {"candidates": candidates, "count": len(candidates)})

    def _handle_admin_gc_execute(self):
        """POST /admin/gc/execute - Execute GC."""
        body = self._read_json_body()
        if body is None:
            return

        digests = body.get("digests")
        operator = body.get("operator")

        if not digests or not isinstance(digests, list):
            self._send_admin_error(400, "MISSING_FIELD", "digests array required")
            return

        if not operator or not isinstance(operator, str):
            self._send_admin_error(400, "MISSING_FIELD", "operator string required")
            return

        if not _OPERATOR_RE.match(operator):
            self._send_admin_error(
                400, "INVALID_OPERATOR", "invalid operator name format"
            )
            return

        for d in digests:
            if not _DIGEST_RE.match(d):
                self._send_admin_error(400, "DIGEST_INVALID", f"invalid digest: {d}")
                return

        store = self._get_store()
        try:
            result = store.gc_execute(digests, operator)
        except StoreError as e:
            self._send_admin_error(400, "GC_ERROR", str(e))
            return

        self._send_json(200, result)

    def _handle_admin_logs(self):
        """GET /admin/logs - Query logs."""
        params = parse_qs(urlparse(self.path).query)
        event_filter = params.get("event", [None])[0]
        since_str = params.get("since", [None])[0]
        limit_str = params.get("limit", ["100"])[0]

        try:
            limit = int(limit_str)
        except ValueError:
            self._send_admin_error(400, "INVALID_PARAM", "limit must be an integer")
            return

        since = None
        if since_str:
            try:
                since = datetime.fromisoformat(since_str)
            except ValueError:
                self._send_admin_error(
                    400, "INVALID_PARAM", "since must be ISO timestamp"
                )
                return

        log_path = getattr(self._server_ref, "log_path", None)
        if log_path is None or not Path(log_path).exists():
            self._send_json(200, [])
            return

        entries = []
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event_filter and entry.get("event") != event_filter:
                    continue

                if since:
                    ts_str = entry.get("ts")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < since:
                                continue
                        except ValueError:
                            continue

                entries.append(entry)
                if len(entries) >= limit:
                    break

        self._send_json(200, entries)

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

        # Try common platforms. V1 limitation: only these platforms are checked.
        # Multi-arch index resolution is not yet supported.
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

    def _read_json_body(self) -> dict | None:
        """Read and parse a JSON request body. Returns None on error."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_admin_error(400, "EMPTY_BODY", "request body required")
            return None

        if content_length > _MAX_JSON_BODY_SIZE:
            self._send_admin_error(
                400, "BODY_TOO_LARGE", "request body exceeds 10 MiB limit"
            )
            return None

        try:
            raw = self.rfile.read(content_length)
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._send_admin_error(
                400, "INVALID_JSON", "request body must be valid JSON"
            )
            return None

    def _send_json(self, status: int, data: object):
        """Send a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_not_found(self):
        """Send a generic 404 response."""
        self._send_oci_error(404, "NOT_FOUND", "resource not found")

    def _send_oci_error(self, status: int, code: str, message: str):
        """Send a standard OCI error response."""
        body = json.dumps({"errors": [{"code": code, "message": message}]}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_admin_error(self, status: int, code: str, message: str):
        """Send a standard Admin API error response."""
        body = json.dumps({"error": message, "code": code}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
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
