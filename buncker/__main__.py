"""CLI entry point for Buncker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

from buncker.config import load_config, save_config
from shared.crypto import derive_keys, generate_mnemonic, split_mnemonic
from shared.logging import setup_logging


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="buncker",
        description="Buncker - Offline Docker Registry",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file (default: /etc/buncker/config.json)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # setup
    sub_setup = subparsers.add_parser("setup", help="Initialize buncker")
    sub_setup.add_argument(
        "--store-path",
        type=Path,
        default=None,
        help="Override store path",
    )

    # serve
    subparsers.add_parser("serve", help="Start the HTTP daemon")

    # analyze
    sub_analyze = subparsers.add_parser("analyze", help="Analyze a Dockerfile")
    sub_analyze.add_argument("dockerfile", type=Path, help="Path to Dockerfile")
    sub_analyze.add_argument(
        "--build-arg",
        action="append",
        default=[],
        help="Build argument (KEY=VALUE)",
    )

    # generate-manifest
    subparsers.add_parser("generate-manifest", help="Generate transfer request")

    # import
    sub_import = subparsers.add_parser("import", help="Import transfer response")
    sub_import.add_argument("file", type=Path, help="Path to .tar.enc file")

    # status
    subparsers.add_parser("status", help="Show registry status")

    # gc
    sub_gc = subparsers.add_parser("gc", help="Garbage collection")
    sub_gc.add_argument(
        "--report",
        action="store_true",
        help="Show GC candidates",
    )
    sub_gc.add_argument(
        "--execute",
        action="store_true",
        help="Execute GC on reported candidates",
    )
    sub_gc.add_argument(
        "--inactive-days",
        type=int,
        default=90,
        help="Inactivity threshold in days (default: 90)",
    )
    sub_gc.add_argument("--operator", type=str, help="Operator name for audit")
    sub_gc.add_argument(
        "--digests",
        nargs="*",
        help="Specific digests to GC",
    )

    # rotate-keys
    sub_rotate = subparsers.add_parser("rotate-keys", help="Rotate crypto keys")
    sub_rotate.add_argument(
        "--grace-period",
        type=int,
        default=30,
        help="Grace period in days (default: 30)",
    )

    # export-ca
    subparsers.add_parser("export-ca", help="Export CA certificate")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "setup":
        _cmd_setup(args)
    elif args.command == "serve":
        _cmd_serve(args)
    elif args.command == "rotate-keys":
        _cmd_rotate_keys(args)
    elif args.command == "export-ca":
        _cmd_export_ca(args)
    else:
        _cmd_proxy(args)


def _cmd_setup(args: argparse.Namespace) -> None:
    """Initialize buncker: generate mnemonic, create config, init store."""
    config_path = args.config or Path("/etc/buncker/config.json")

    if config_path.exists():
        print(f"Config already exists at {config_path}")
        sys.exit(1)

    import base64

    # Generate 16-word mnemonic (12 secret + 4 salt words)
    mnemonic = generate_mnemonic()
    mnemonic_12, salt = split_mnemonic(mnemonic)

    # Compute mnemonic hash for config verification (uses 12-word part)
    mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic_12.encode()).hexdigest()}"

    # Determine store path
    store_path = str(args.store_path) if args.store_path else "/var/lib/buncker"

    config = {
        "source_id": f"buncker-{os.urandom(4).hex()}",
        "bind": "0.0.0.0",
        "port": 5000,
        "store_path": store_path,
        "max_workers": 16,
        "tls": False,
        "crypto": {
            "salt": base64.b64encode(salt).decode(),
            "mnemonic_hash": mnemonic_hash,
        },
        "private_registries": [],
        "gc": {"inactive_days_threshold": 90},
        "log_level": "INFO",
    }

    # Save config
    save_config(config, config_path)

    # Initialize store
    store_dir = Path(store_path)
    store_dir.mkdir(parents=True, exist_ok=True)

    from buncker.store import Store

    Store(store_dir)

    # Display mnemonic
    print("Buncker initialized successfully.")
    print()
    print("IMPORTANT: Write down the following 12-word mnemonic.")
    print("This is the ONLY time it will be displayed.")
    print("You need it to start the daemon and for key recovery.")
    print()
    print(f"  {mnemonic}")
    print()
    print(f"Config: {config_path}")
    print(f"Store:  {store_path}")


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the HTTP daemon."""
    import base64

    config = load_config(args.config)
    setup_logging(
        level=config.get("log_level", "INFO"),
        output_path=Path(config["store_path"]) / "buncker.log",
    )

    # Get mnemonic from env or stdin
    mnemonic = os.environ.get("BUNCKER_MNEMONIC")
    if not mnemonic:
        try:
            mnemonic = input("Enter mnemonic: ").strip()
        except EOFError:
            print(
                "Error: mnemonic required (set BUNCKER_MNEMONIC or provide via stdin)"
            )
            sys.exit(1)

    if not mnemonic:
        print("Error: mnemonic cannot be empty")
        sys.exit(1)

    # Split 16-word mnemonic into 12-word secret + salt
    try:
        mnemonic_12, salt = split_mnemonic(mnemonic)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # Verify mnemonic hash (uses 12-word part)
    crypto_config = config.get("crypto", {})
    expected_hash = crypto_config.get("mnemonic_hash", "")
    actual_hash = f"sha256:{hashlib.sha256(mnemonic_12.encode()).hexdigest()}"

    if expected_hash and actual_hash != expected_hash:
        print("Error: mnemonic does not match config - wrong mnemonic")
        sys.exit(1)

    # Derive keys from 12-word mnemonic + embedded salt
    aes_key, hmac_key = derive_keys(mnemonic_12, salt)

    # Initialize store and server
    from buncker.server import BunckerServer
    from buncker.store import Store

    store = Store(Path(config["store_path"]))
    server = BunckerServer(
        bind=config.get("bind", "0.0.0.0"),
        port=config.get("port", 5000),
        store=store,
        max_workers=config.get("max_workers", 16),
        crypto_keys=(aes_key, hmac_key),
        source_id=config.get("source_id", ""),
        log_path=Path(config["store_path"]) / "buncker.log",
    )

    # Handle SIGTERM/SIGINT
    shutdown_event = threading.Event()

    def _shutdown(signum, frame):
        server.stop()
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server.start()
    print(f"Buncker serving on {config.get('bind')}:{config.get('port')}")

    # Block until shutdown signal (cross-platform)
    shutdown_event.wait()


def _cmd_rotate_keys(args: argparse.Namespace) -> None:
    """Generate new mnemonic and update config."""
    import base64

    config_path = args.config or Path("/etc/buncker/config.json")
    config = load_config(config_path)

    from datetime import UTC, datetime

    # Save old crypto config with grace period
    old_crypto = config.get("crypto", {})
    old_crypto["deprecated_at"] = datetime.now(tz=UTC).isoformat()
    old_crypto["grace_period_days"] = args.grace_period

    # Generate new 16-word mnemonic (12 secret + 4 salt)
    mnemonic = generate_mnemonic()
    mnemonic_12, salt = split_mnemonic(mnemonic)
    mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic_12.encode()).hexdigest()}"

    config["crypto"] = {
        "salt": base64.b64encode(salt).decode(),
        "mnemonic_hash": mnemonic_hash,
        "previous": old_crypto,
    }

    save_config(config, config_path)

    print("Keys rotated successfully.")
    print()
    print("IMPORTANT: Write down the following NEW 16-word mnemonic.")
    print("This is the ONLY time it will be displayed.")
    print()
    print(f"  {mnemonic}")
    print()
    print(f"Grace period: {args.grace_period} days (old keys kept in config)")
    print("Restart the daemon with the new mnemonic.")


def _cmd_export_ca(args: argparse.Namespace) -> None:
    """Export CA certificate if TLS is enabled."""
    config = load_config(args.config)
    if not config.get("tls"):
        print("TLS is not enabled. No CA certificate to export.")
        print("Enable TLS in config to use this command.")
        sys.exit(0)

    ca_path = Path(config["store_path"]) / "tls" / "ca.pem"
    if not ca_path.exists():
        print(f"CA certificate not found at {ca_path}")
        sys.exit(1)

    print(ca_path.read_text())


def _cmd_proxy(args: argparse.Namespace) -> None:
    """Proxy CLI commands to the admin API."""
    config = load_config(args.config)
    port = config.get("port", 5000)
    base = f"http://localhost:{port}"

    if args.command == "analyze":
        build_args = {}
        for ba in args.build_arg:
            key, _, value = ba.partition("=")
            build_args[key] = value

        data = {"dockerfile": str(args.dockerfile)}
        if build_args:
            data["build_args"] = build_args

        result = _admin_post(f"{base}/admin/analyze", data)
        print(json.dumps(result, indent=2))

    elif args.command == "generate-manifest":
        result = _admin_post_raw(f"{base}/admin/generate-manifest", {})
        if isinstance(result, bytes):
            # Save to file
            filename = "buncker-request.json.enc"
            Path(filename).write_bytes(result)
            print(f"Transfer request saved to {filename}")
        else:
            print(json.dumps(result, indent=2))

    elif args.command == "import":
        file_data = args.file.read_bytes()
        result = _admin_post_binary(f"{base}/admin/import", file_data)
        print(json.dumps(result, indent=2))

    elif args.command == "status":
        result = _admin_get(f"{base}/admin/status")
        print(json.dumps(result, indent=2))

    elif args.command == "gc":
        if args.report or not args.execute:
            result = _admin_get(
                f"{base}/admin/gc/report?inactive_days={args.inactive_days}"
            )
            print(json.dumps(result, indent=2))
        if args.execute:
            digests = args.digests or []
            operator = args.operator or "cli"
            data = {"digests": digests, "operator": operator}
            result = _admin_post(f"{base}/admin/gc/execute", data)
            print(json.dumps(result, indent=2))


def _admin_get(url: str) -> dict:
    """Make a GET request to the admin API."""
    try:
        resp = urllib.request.urlopen(url)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            print(f"Error {e.code}: {body.decode()}", file=sys.stderr)
            sys.exit(1)
    except URLError as e:
        print(f"Cannot connect to buncker daemon: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _admin_post(url: str, data: dict) -> dict:
    """Make a POST request with JSON body."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            print(f"Error {e.code}: {body.decode()}", file=sys.stderr)
            sys.exit(1)
    except URLError as e:
        print(f"Cannot connect to buncker daemon: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _admin_post_raw(url: str, data: dict) -> bytes | dict:
    """Make a POST and return raw bytes if binary, or parsed JSON."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()
        if "application/json" in content_type:
            return json.loads(raw)
        return raw
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            print(f"Error {e.code}: {body.decode()}", file=sys.stderr)
            sys.exit(1)
    except URLError as e:
        print(f"Cannot connect to buncker daemon: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _admin_post_binary(url: str, data: bytes) -> dict:
    """Make a POST request with binary body."""
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
        },
    )
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            print(f"Error {e.code}: {body.decode()}", file=sys.stderr)
            sys.exit(1)
    except URLError as e:
        print(f"Cannot connect to buncker daemon: {e.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
