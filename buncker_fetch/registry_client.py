"""Online OCI registry client with auth discovery and retry."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

from shared.exceptions import RegistryError

_log = logging.getLogger("buncker.fetch.registry_client")


class _NoAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that strips Authorization on cross-host redirects.

    Docker Hub returns 307 redirects to CDN for blob downloads.
    The CDN rejects the Bearer token with HTTP 400, so we must
    remove the Authorization header when the redirect target
    differs from the original host.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,  # noqa: ANN001
        code: int,
        msg: str,
        headers: dict,
        newurl: str,
    ) -> urllib.request.Request | None:
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            # Strip auth if redirected to a different host
            original_host = urllib.parse.urlparse(req.full_url).netloc
            redirect_host = urllib.parse.urlparse(newurl).netloc
            if original_host != redirect_host:
                new_req.remove_header("Authorization")
        return new_req


_CONNECT_TIMEOUT = 30
_READ_TIMEOUT = 120
_MAX_RETRIES = 3
_BACKOFF_BASE = 1  # 1s, 3s, 9s

_OCI_MANIFEST_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)

_WWW_AUTH_RE = re.compile(
    r'Bearer\s+realm="([^"]+)"'
    r'(?:,\s*service="([^"]*)")?'
    r'(?:,\s*scope="([^"]*)")?',
    re.IGNORECASE,
)


class RegistryClient:
    """OCI registry client with Bearer token auth and retry.

    Args:
        registry: Registry hostname (e.g. "registry-1.docker.io").
        credentials: Optional dict with "username" and "password" keys.
    """

    def __init__(
        self,
        registry: str,
        credentials: dict[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self._credentials = credentials
        self._tokens: dict[str, str] = {}
        self._opener = urllib.request.build_opener(_NoAuthRedirectHandler)

    def fetch_manifest(self, repository: str, reference: str) -> dict:
        """Fetch a manifest from the registry.

        Args:
            repository: Image repository (e.g. "library/nginx").
            reference: Tag or digest reference.

        Returns:
            Parsed manifest JSON dict.

        Raises:
            RegistryError: On fetch failure.
        """
        path = f"/v2/{repository}/manifests/{reference}"
        accept = ", ".join(_OCI_MANIFEST_TYPES)
        headers = {"Accept": accept}
        scope = f"repository:{repository}:pull"
        data = self._request(path, scope=scope, extra_headers=headers)
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise RegistryError(
                f"Invalid manifest JSON from {self.registry}",
                {"repository": repository, "reference": reference},
            ) from exc

    def fetch_blob(self, repository: str, digest: str) -> Iterator[bytes]:
        """Fetch a blob from the registry as a streaming iterator.

        Args:
            repository: Image repository.
            digest: Blob digest (sha256:...).

        Yields:
            Chunks of blob data (64KB each).

        Raises:
            RegistryError: On fetch failure.
        """
        path = f"/v2/{repository}/blobs/{digest}"
        scope = f"repository:{repository}:pull"
        yield from self._stream(path, scope=scope)

    def _get_token(self, scope: str) -> str | None:
        """Get a Bearer token for the given scope, using cache."""
        if scope in self._tokens:
            return self._tokens[scope]

        # Auth discovery: GET /v2/ to trigger 401
        url = f"https://{self.registry}/v2/"
        req = urllib.request.Request(url, method="GET")

        try:
            response = urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT)
            # 200 means no auth needed
            response.read()
            return None
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise RegistryError(
                    f"Auth discovery failed: HTTP {exc.code} from {self.registry}",
                    {"url": url, "status": exc.code},
                ) from exc

            www_auth = exc.headers.get("Www-Authenticate", "")
            match = _WWW_AUTH_RE.search(www_auth)
            if not match:
                raise RegistryError(
                    f"Cannot parse Www-Authenticate header from {self.registry}",
                    {"header": www_auth},
                ) from exc

            realm = match.group(1)
            service = match.group(2) or ""
            # Use the scope we need, not the one from the header
            return self._exchange_token(realm, service, scope)
        except urllib.error.URLError as exc:
            raise RegistryError(
                f"Connection failed to {self.registry}",
                {"url": url, "error": str(exc)},
            ) from exc

    def _exchange_token(self, realm: str, service: str, scope: str) -> str:
        """Exchange credentials for a Bearer token."""
        params = f"service={_quote(service)}&scope={_quote(scope)}"
        token_url = f"{realm}?{params}"

        req = urllib.request.Request(token_url, method="GET")

        if self._credentials:
            user = self._credentials["username"]
            passwd = self._credentials["password"]
            basic = base64.b64encode(f"{user}:{passwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {basic}")

        try:
            response = urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT)
            body = json.loads(response.read())
            token = body.get("token") or body.get("access_token", "")
            self._tokens[scope] = token
            return token
        except urllib.error.HTTPError as exc:
            raise RegistryError(
                f"Token exchange failed: HTTP {exc.code}",
                {"realm": realm, "service": service, "scope": scope},
            ) from exc
        except urllib.error.URLError as exc:
            raise RegistryError(
                "Token exchange connection failed",
                {"realm": realm, "error": str(exc)},
            ) from exc

    def _build_request(
        self,
        path: str,
        scope: str,
        extra_headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        """Build an authenticated request."""
        url = f"https://{self.registry}{path}"
        req = urllib.request.Request(url, method="GET")

        token = self._get_token(scope)
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        if extra_headers:
            for key, value in extra_headers.items():
                req.add_header(key, value)

        return req

    def _request(
        self,
        path: str,
        scope: str,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        """Make an authenticated request with retry."""
        req = self._build_request(path, scope, extra_headers)

        for attempt in range(_MAX_RETRIES):
            try:
                response = self._opener.open(req, timeout=_READ_TIMEOUT)
                return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    # Token may have expired, re-authenticate
                    self._tokens.pop(scope, None)
                    req = self._build_request(path, scope, extra_headers)
                    continue
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After", "")
                    _log.warning(
                        "registry_rate_limited",
                        extra={
                            "registry": self.registry,
                            "retry_after": retry_after,
                        },
                    )
                    raise RegistryError(
                        f"Rate limited by {self.registry}. "
                        f"Retry after {retry_after or 'unknown'}s",
                        {"registry": self.registry, "status": 429},
                    ) from exc
                if exc.code >= 500 and attempt < _MAX_RETRIES - 1:
                    wait = _BACKOFF_BASE * (3**attempt)
                    _log.info(
                        "registry_retry",
                        extra={
                            "attempt": attempt + 1,
                            "wait": wait,
                            "status": exc.code,
                        },
                    )
                    time.sleep(wait)
                    continue
                raise RegistryError(
                    f"Registry request failed: HTTP {exc.code}",
                    {"url": req.full_url, "status": exc.code},
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < _MAX_RETRIES - 1:
                    wait = _BACKOFF_BASE * (3**attempt)
                    time.sleep(wait)
                    continue
                raise RegistryError(
                    f"Connection failed to {self.registry}",
                    {"url": req.full_url, "error": str(exc)},
                ) from exc

        raise RegistryError(
            f"All {_MAX_RETRIES} attempts failed for {path}",
            {"registry": self.registry},
        )

    def _stream(
        self,
        path: str,
        scope: str,
    ) -> Iterator[bytes]:
        """Stream an authenticated request with retry.

        Collects all chunks before yielding to avoid duplicate data
        if a retry occurs after partial read.
        """
        data = self._request(path, scope)
        for offset in range(0, len(data), 65536):
            yield data[offset : offset + 65536]


def load_credentials(config: dict, registry: str) -> dict[str, str] | None:
    """Load registry credentials from environment variables.

    Args:
        config: Config dict with "registries" mapping.
        registry: Registry hostname.

    Returns:
        Dict with "username" and "password", or None if not configured.
    """
    registries = config.get("registries", {})
    reg_config = registries.get(registry)
    if not reg_config:
        return None

    user_env = reg_config.get("user_env", "")
    pass_env = reg_config.get("pass_env", "")

    username = os.environ.get(user_env, "")
    password = os.environ.get(pass_env, "")

    if not username or not password:
        return None

    return {"username": username, "password": password}


def _quote(s: str) -> str:
    """URL-encode a string for query parameters."""
    return urllib.request.quote(s, safe="")
