"""HTTP server for Buncker - OCI Distribution API + Admin API."""

from __future__ import annotations

import collections
import logging
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

from buncker.handler import BunckerHandler
from buncker.store import Store

_log = logging.getLogger("buncker.server")

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 60  # requests per window per IP


class RateLimiter:
    """Per-IP sliding window rate limiter for admin endpoints."""

    def __init__(
        self, max_requests: int = _RATE_LIMIT_MAX, window: int = _RATE_LIMIT_WINDOW
    ) -> None:
        self._max = max_requests
        self._window = window
        self._hits: dict[str, collections.deque] = {}
        self._lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        """Check if a request from *ip* is within the rate limit."""
        now = time.monotonic()
        with self._lock:
            if ip not in self._hits:
                self._hits[ip] = collections.deque()
            q = self._hits[ip]
            # Evict expired entries
            while q and q[0] <= now - self._window:
                q.popleft()
            if len(q) >= self._max:
                return False
            q.append(now)
            return True


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a bounded thread pool.

    Limits concurrent request handling to ``max_workers`` threads
    to prevent resource exhaustion under high load.
    """

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class,
        *,
        max_workers: int = 16,
    ) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address) -> None:
        """Submit request processing to the bounded thread pool."""
        self._pool.submit(self.process_request_thread, request, client_address)

    def server_close(self) -> None:
        """Shut down the thread pool when closing the server."""
        super().server_close()
        self._pool.shutdown(wait=False)


class BunckerServer:
    """Threaded HTTP server with bounded thread pool for OCI registry.

    Args:
        bind: Address to bind to.
        port: Port to listen on.
        store: Store instance for blob operations.
        max_workers: Maximum concurrent request threads.
        crypto_keys: Optional (aes_key, hmac_key) tuple for transfer operations.
    """

    def __init__(
        self,
        bind: str = "0.0.0.0",
        port: int = 5000,
        *,
        store: Store,
        max_workers: int = 16,
        crypto_keys: tuple[bytes, bytes] | None = None,
        source_id: str = "",
        log_path: Path | None = None,
        api_tokens: dict[str, str] | None = None,
        api_enabled: bool = False,
        tls_cert: Path | None = None,
        tls_key: Path | None = None,
        oci_restrict: bool = False,
    ) -> None:
        self._bind = bind
        self._port = port
        self._store = store
        self._max_workers = max_workers
        self._server: _BoundedThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.crypto_keys = crypto_keys
        self.source_id = source_id
        self.log_path = log_path
        self.api_tokens = api_tokens
        self.api_enabled = api_enabled
        self._tls_cert = tls_cert
        self._tls_key = tls_key
        self.oci_restrict = oci_restrict
        self._start_time: float | None = None
        self._last_analysis = None
        self._analysis_lock = threading.Lock()
        self.rate_limiter = RateLimiter()

    def start(self) -> None:
        """Start the server in a background thread."""

        def handler_factory(*args, **kwargs):
            return BunckerHandler(*args, server_ref=self, **kwargs)

        self._server = _BoundedThreadingHTTPServer(
            (self._bind, self._port),
            handler_factory,
            max_workers=self._max_workers,
        )

        # Wrap socket with TLS if cert/key provided
        if self._tls_cert and self._tls_key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self._tls_cert, self._tls_key)
            self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)

        self._start_time = time.time()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        scheme = "https" if self._tls_cert else "http"
        _log.info(
            "server_started",
            extra={"bind": self._bind, "port": self.port, "scheme": scheme},
        )

    def stop(self) -> None:
        """Shut down the server gracefully."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            _log.info("server_stopped")

    @property
    def port(self) -> int:
        """Return the actual port (useful when binding to port 0)."""
        if self._server is not None:
            return self._server.server_address[1]
        return self._port

    @property
    def store(self) -> Store:
        """Return the store instance."""
        return self._store
