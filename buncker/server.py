"""HTTP server for Buncker - OCI Distribution API + Admin API."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path

from buncker.handler import BunckerHandler
from buncker.store import Store

_log = logging.getLogger("buncker.server")


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
    ) -> None:
        self._bind = bind
        self._port = port
        self._store = store
        self._max_workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.crypto_keys = crypto_keys
        self.source_id = source_id
        self.log_path = log_path
        self._start_time: float | None = None
        self._last_analysis = None

    def start(self) -> None:
        """Start the server in a background thread."""

        def handler_factory(*args, **kwargs):
            return BunckerHandler(*args, server_ref=self, **kwargs)

        self._server = HTTPServer((self._bind, self._port), handler_factory)
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        _log.info(
            "server_started",
            extra={"bind": self._bind, "port": self.port},
        )

    def stop(self) -> None:
        """Shut down the server gracefully."""
        if self._server is not None:
            self._server.shutdown()
            self._pool.shutdown(wait=False)
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

    @property
    def pool(self) -> ThreadPoolExecutor:
        """Return the thread pool executor."""
        return self._pool
