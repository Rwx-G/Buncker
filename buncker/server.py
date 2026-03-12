"""HTTP server for Buncker - OCI Distribution API + Admin API."""

from __future__ import annotations

import collections
import logging
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

import waitress

from buncker.handler import create_wsgi_app
from buncker.store import Store

_log = logging.getLogger("buncker.server")

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 60  # requests per window per IP

# 40 GiB max request body (streaming PUT import)
_MAX_BODY = 40 * 1024 * 1024 * 1024


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


class _QuietWSGIHandler(WSGIRequestHandler):
    """WSGIRequestHandler that suppresses default stderr logging."""

    def log_request(self, *args, **kwargs):
        pass


class _BoundedWSGIServer(ThreadingMixIn, WSGIServer):
    """Threaded WSGI server with bounded thread pool.

    Used as TLS fallback when waitress is not suitable (waitress does
    not support TLS natively due to its async I/O model).
    """

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        app,
        *,
        max_workers: int = 16,
    ) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        super().__init__(server_address, _QuietWSGIHandler)
        self.set_app(app)

    def process_request(self, request, client_address) -> None:
        """Submit request processing to the bounded thread pool."""
        self._pool.submit(self.process_request_thread, request, client_address)

    def server_close(self) -> None:
        """Shut down the thread pool when closing the server."""
        super().server_close()
        self._pool.shutdown(wait=False)


class BunckerServer:
    """HTTP server for OCI registry with WSGI backend.

    Uses waitress (production-grade) for plain HTTP and falls back to
    stdlib WSGIServer with thread pool for TLS (waitress does not
    support SSL sockets in its async event loop).

    Args:
        bind: Address to bind to.
        port: Port to listen on.
        store: Store instance for blob operations.
        max_workers: Maximum concurrent request threads.
        crypto_keys: Optional (aes_key, hmac_key) tuple for transfer operations.
    """

    def __init__(
        self,
        bind: str = "127.0.0.1",
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
        manifest_ttl: int = 30,
    ) -> None:
        self._bind = bind
        self._port = port
        self._store = store
        self._max_workers = max_workers
        self._server = None
        self._thread: threading.Thread | None = None
        self._use_tls = bool(tls_cert and tls_key)
        self.crypto_keys = crypto_keys
        self.source_id = source_id
        self.log_path = log_path
        self.api_tokens = api_tokens
        self.api_enabled = api_enabled
        self._tls_cert = tls_cert
        self._tls_key = tls_key
        self.oci_restrict = oci_restrict
        self.manifest_ttl = manifest_ttl
        self._start_time: float | None = None
        self._actual_port: int = port
        self._last_analysis = None
        self._analysis_lock = threading.Lock()
        self.rate_limiter = RateLimiter()

    def start(self) -> None:
        """Start the server in a background thread."""
        app = create_wsgi_app(self)

        if self._use_tls:
            self._start_tls(app)
        else:
            self._start_waitress(app)

        self._start_time = time.time()
        scheme = "https" if self._use_tls else "http"
        _log.info(
            "server_started",
            extra={"bind": self._bind, "port": self.port, "scheme": scheme},
        )

    def _start_waitress(self, app) -> None:
        """Start with waitress (production-grade, no TLS)."""
        self._server = waitress.create_server(
            app,
            host=self._bind,
            port=self._port,
            threads=self._max_workers,
            channel_timeout=60,
            max_request_body_size=_MAX_BODY,
        )
        # Resolve actual port (needed when port=0)
        self._actual_port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(
            target=self._run_waitress_loop, daemon=True
        )
        self._thread.start()

    def _run_waitress_loop(self) -> None:
        """Run the waitress event loop, suppressing expected shutdown errors.

        On Windows, closing the server socket while the event loop is
        blocked in select() raises OSError (WinError 10038).  This is
        harmless during intentional shutdown.
        """
        try:
            self._server.run()
        except OSError:
            pass

    def _start_tls(self, app) -> None:
        """Start with stdlib WSGI server + TLS (fallback for SSL)."""
        self._server = _BoundedWSGIServer(
            (self._bind, self._port),
            app,
            max_workers=self._max_workers,
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")
        ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
        ctx.load_cert_chain(self._tls_cert, self._tls_key)
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
        self._actual_port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server gracefully."""
        if self._server is not None:
            if self._use_tls:
                self._server.shutdown()
                self._server.server_close()
            else:
                self._server.close()
            if self._thread is not None:
                self._thread.join(timeout=2)
            _log.info("server_stopped")

    @property
    def port(self) -> int:
        """Return the actual port (useful when binding to port 0)."""
        return self._actual_port

    @property
    def store(self) -> Store:
        """Return the store instance."""
        return self._store
