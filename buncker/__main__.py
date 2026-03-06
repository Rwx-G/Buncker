"""CLI entry point for Buncker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

from buncker.config import load_config, save_config
from shared.crypto import derive_keys, generate_mnemonic, split_mnemonic
from shared.logging import setup_logging

# ANSI color codes
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _c(text: str, code: str) -> str:
    """Colorize text if stdout is a TTY."""
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{_RESET}"


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
    sub_gen = subparsers.add_parser(
        "generate-manifest", help="Generate transfer request"
    )
    sub_gen.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for transfer request",
    )

    # prepare (analyze + generate-manifest in one step)
    sub_prepare = subparsers.add_parser(
        "prepare", help="Analyze Dockerfile and generate transfer request"
    )
    sub_prepare.add_argument("dockerfile", type=Path, help="Path to Dockerfile")
    sub_prepare.add_argument(
        "--build-arg",
        action="append",
        default=[],
        help="Build argument (KEY=VALUE)",
    )
    sub_prepare.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for transfer request",
    )

    # import
    sub_import = subparsers.add_parser("import", help="Import transfer response")
    sub_import.add_argument(
        "file",
        type=Path,
        nargs="?",
        default=None,
        help="Path to .tar.enc file (auto-scans transfer_path if omitted)",
    )

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

    # api-setup
    sub_api_setup = subparsers.add_parser(
        "api-setup", help="Generate API tokens and activate TLS"
    )
    sub_api_setup.add_argument(
        "--cert", type=Path, default=None, help="Path to TLS certificate"
    )
    sub_api_setup.add_argument(
        "--key", type=Path, default=None, help="Path to TLS private key"
    )

    # api-show
    sub_api_show = subparsers.add_parser("api-show", help="Display an API token")
    sub_api_show.add_argument(
        "token_type",
        choices=["readonly", "admin"],
        help="Token type to display",
    )

    # api-reset
    sub_api_reset = subparsers.add_parser("api-reset", help="Regenerate an API token")
    sub_api_reset.add_argument(
        "token_type",
        choices=["readonly", "admin"],
        help="Token type to regenerate",
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
    elif args.command == "prepare":
        _cmd_prepare(args)
    elif args.command == "rotate-keys":
        _cmd_rotate_keys(args)
    elif args.command == "export-ca":
        _cmd_export_ca(args)
    elif args.command == "api-setup":
        _cmd_api_setup(args)
    elif args.command == "api-show":
        _cmd_api_show(args)
    elif args.command == "api-reset":
        _cmd_api_reset(args)
    else:
        _cmd_proxy(args)


def _cmd_setup(args: argparse.Namespace) -> None:
    """Initialize buncker: generate mnemonic, create config, init store."""
    config_path = args.config or Path("/etc/buncker/config.json")

    if config_path.exists():
        print(f"Config already exists at {config_path}")
        sys.exit(1)

    import base64

    # [1/4] Generate cryptographic keys
    print(
        f"{_c('[1/4]', _BOLD)} Generating cryptographic keys... ",
        end="",
        flush=True,
    )
    mnemonic = generate_mnemonic()
    mnemonic_12, salt = split_mnemonic(mnemonic)
    mnemonic_hash = f"sha256:{hashlib.sha256(mnemonic_12.encode()).hexdigest()}"
    print(_c("done", _GREEN))

    # [2/4] Initialize store
    store_path = str(args.store_path) if args.store_path else "/var/lib/buncker"
    print(
        f"{_c('[2/4]', _BOLD)} Initializing store...             ",
        end="",
        flush=True,
    )
    store_dir = Path(store_path)
    store_dir.mkdir(parents=True, exist_ok=True)
    from buncker.store import Store

    Store(store_dir)
    print(_c("done", _GREEN))

    # [3/4] Save configuration
    print(
        f"{_c('[3/4]', _BOLD)} Saving configuration...           ",
        end="",
        flush=True,
    )
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
    save_config(config, config_path)

    # Save mnemonic to env file for systemd
    env_path = config_path.parent / "env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(f"BUNCKER_MNEMONIC={mnemonic}\n", encoding="utf-8")
    import contextlib

    with contextlib.suppress(OSError):
        env_path.chmod(0o600)
    print(_c("done", _GREEN))

    # [4/4] Enable and start daemon
    print(
        f"{_c('[4/4]', _BOLD)} Enabling and starting daemon...   ",
        end="",
        flush=True,
    )
    daemon_status = "active"
    try:
        subprocess.run(
            ["systemctl", "enable", "--now", "buncker"],
            check=True,
            capture_output=True,
        )
        bind = config.get("bind", "0.0.0.0")
        port = config.get("port", 5000)
        daemon_status = f"active on {bind}:{port}"
        print(_c("done", _GREEN))
    except (subprocess.CalledProcessError, FileNotFoundError):
        daemon_status = "not started (systemctl unavailable or not root)"
        print(_c("skipped", _YELLOW))
        print(
            f"  {_c('Warning:', _YELLOW)} Could not enable daemon. "
            "Start manually with: sudo systemctl enable --now buncker"
        )

    # Display mnemonic
    words = mnemonic.split()
    line1 = " ".join(words[:8])
    line2 = " ".join(words[8:])

    sep = "=" * 60
    print()
    print(_c(sep, _DIM))
    print()
    print(f"  {_c('IMPORTANT', _BOLD)} - Write down your 16-word recovery mnemonic.")
    print("  This is the ONLY time it will be displayed.")
    print()
    print(f"  {_c(line1, _BOLD + _YELLOW)}")
    print(f"  {_c(line2, _BOLD + _YELLOW)}")
    print()
    print(f"  Config:  {config_path}")
    print(f"  Store:   {store_path}")
    print(f"  Daemon:  {daemon_status}")
    print()
    print(_c(sep, _DIM))


def _cmd_api_setup(args: argparse.Namespace) -> None:
    """Generate API tokens and activate TLS for LAN access."""
    config_path = args.config or Path("/etc/buncker/config.json")

    if not config_path.exists():
        print(f"{_c('Error:', _RED)} Config not found at {config_path}")
        print("Run 'buncker setup' first.")
        sys.exit(1)

    config = load_config(config_path)

    # Check if api-setup was already run
    tokens_path = config_path.parent / "api-tokens.json"
    if tokens_path.exists():
        print(f"{_c('Warning:', _YELLOW)} API tokens already exist at {tokens_path}")
        print("Use 'buncker api-reset' to regenerate individual tokens.")
        sys.exit(1)

    from buncker.auth import generate_api_tokens, generate_self_signed_cert, save_api_tokens

    # [1/3] Generate tokens
    print(
        f"{_c('[1/3]', _BOLD)} Generating API tokens...          ",
        end="",
        flush=True,
    )
    tokens = generate_api_tokens()
    save_api_tokens(tokens, tokens_path)
    print(_c("done", _GREEN))

    # [2/3] TLS setup
    print(
        f"{_c('[2/3]', _BOLD)} Configuring TLS...                ",
        end="",
        flush=True,
    )
    store_path = Path(config["store_path"])
    tls_dir = store_path / "tls"

    if args.cert and args.key:
        # User-provided certificate
        if not args.cert.exists():
            print(_c("failed", _RED))
            print(f"  {_c('Error:', _RED)} Certificate not found: {args.cert}")
            sys.exit(1)
        if not args.key.exists():
            print(_c("failed", _RED))
            print(f"  {_c('Error:', _RED)} Key not found: {args.key}")
            sys.exit(1)
        tls_dir.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(args.cert, tls_dir / "server.pem")
        shutil.copy2(args.key, tls_dir / "server-key.pem")
        print(_c("done", _GREEN))
        print(f"  Using provided certificate: {args.cert}")
    else:
        # Auto-signed certificate
        cert_path, key_path, ca_path = generate_self_signed_cert(tls_dir)
        print(_c("done", _GREEN))
        print(
            f"  {_c('Warning:', _YELLOW)} Auto-signed certificate generated."
        )
        print(
            "  Clients must trust the CA. Export with: buncker export-ca"
        )

    # [3/3] Update config
    print(
        f"{_c('[3/3]', _BOLD)} Updating configuration...          ",
        end="",
        flush=True,
    )
    config["api"] = {"enabled": True}
    config["tls"] = True
    save_config(config, config_path)
    print(_c("done", _GREEN))

    # Display tokens
    sep = "=" * 60
    print()
    print(_c(sep, _DIM))
    print()
    print(f"  {_c('API TOKENS', _BOLD)} - Save these securely.")
    print("  This is the ONLY time they will be displayed together.")
    print()
    print(f"  {_c('Read-only:', _BOLD)} {tokens['readonly']}")
    print(f"  {_c('Admin:', _BOLD)}     {tokens['admin']}")
    print()
    print(f"  Tokens file: {tokens_path}")
    print(f"  TLS certs:   {tls_dir}")
    print()
    print("  Restart the daemon to apply changes:")
    print("    sudo systemctl restart buncker")
    print()
    print(_c(sep, _DIM))


def _cmd_api_show(args: argparse.Namespace) -> None:
    """Display an API token."""
    config_path = args.config or Path("/etc/buncker/config.json")
    tokens_path = config_path.parent / "api-tokens.json"

    from buncker.auth import load_api_tokens

    tokens = load_api_tokens(tokens_path)
    if tokens is None:
        print(f"{_c('Error:', _RED)} API tokens not found at {tokens_path}")
        print("Run 'buncker api-setup' first.")
        sys.exit(1)

    print(tokens[args.token_type])


def _cmd_api_reset(args: argparse.Namespace) -> None:
    """Regenerate an API token."""
    import logging

    config_path = args.config or Path("/etc/buncker/config.json")
    tokens_path = config_path.parent / "api-tokens.json"

    from buncker.auth import load_api_tokens, save_api_tokens

    tokens = load_api_tokens(tokens_path)
    if tokens is None:
        print(f"{_c('Error:', _RED)} API tokens not found at {tokens_path}")
        print("Run 'buncker api-setup' first.")
        sys.exit(1)

    import secrets

    tokens[args.token_type] = secrets.token_hex(32)
    save_api_tokens(tokens, tokens_path)

    _log = logging.getLogger("buncker.auth")
    _log.info("api_token_reset", extra={"token_type": args.token_type})

    print(f"New {args.token_type} token: {tokens[args.token_type]}")
    print()
    print("The old token is now invalid.")
    print("Restart the daemon to apply: sudo systemctl restart buncker")


def _cmd_prepare(args: argparse.Namespace) -> None:
    """Analyze Dockerfile and generate transfer request in one step."""
    config = load_config(args.config)
    port = config.get("port", 5000)
    base = f"http://localhost:{port}"

    # Step 1: Analyze
    dockerfile = args.dockerfile.resolve()
    print(f"Analyzing {_c(str(dockerfile), _BOLD)}...")

    build_args = {}
    for ba in args.build_arg:
        key, _, value = ba.partition("=")
        build_args[key] = value

    data = {"dockerfile": str(dockerfile)}
    if build_args:
        data["build_args"] = build_args

    analysis = _admin_post(f"{base}/admin/analyze", data)
    if "error" in analysis:
        print(f"  {_c('Error:', _RED)} {analysis.get('message', analysis)}")
        sys.exit(1)

    images = analysis.get("images", [])
    external = [img for img in images if not img.get("is_internal")]
    missing_count = len(analysis.get("missing_blobs", []))

    print(f"  Images: {_c(str(len(external)), _BOLD)}")
    for img in external:
        print(f"    {img.get('resolved', img.get('raw', ''))}")
    print(f"  Missing blobs: {_c(str(missing_count), _BOLD)}")

    # Step 2: Generate manifest
    print()
    print("Generating transfer request...")

    result = _admin_post_raw(f"{base}/admin/generate-manifest", {})
    if isinstance(result, dict) and "error" in result:
        print(f"  {_c('Error:', _RED)} {result.get('message', result)}")
        sys.exit(1)

    if isinstance(result, bytes):
        # Determine output path
        output_dir = args.output or _resolve_transfer_path(config) or Path.cwd()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "buncker-request.json.enc"
        out_file.write_bytes(result)
        size = len(result)
        print(f"  Output: {_c(str(out_file), _BOLD)} ({size} B)")
    else:
        print(json.dumps(result, indent=2))
        return

    # Hint for next step
    print()
    print("Next: copy to online machine and run:")
    print(f"  buncker-fetch fetch {out_file.name} --output /media/usb/")


def _resolve_transfer_path(config: dict) -> Path | None:
    """Return transfer_path from config if set, else None."""
    tp = config.get("transfer_path", "")
    if tp:
        return Path(tp)
    return None


def _cmd_serve(args: argparse.Namespace) -> None:
    """Start the HTTP daemon."""
    config = load_config(args.config)
    setup_logging(
        level=config.get("log_level", "INFO"),
        output_path=Path(config["store_path"]) / "buncker.log",
    )

    # Refuse to start if API is enabled without TLS
    api_config = config.get("api", {})
    if api_config.get("enabled") and not config.get("tls"):
        print("Error: API authentication is enabled but TLS is not.")
        print("TLS is mandatory when the API is exposed. Run 'buncker api-setup'.")
        sys.exit(1)

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

    # Load API tokens if auth is enabled
    api_tokens = None
    api_enabled = api_config.get("enabled", False)
    if api_enabled:
        from buncker.auth import load_api_tokens

        config_dir = (args.config or Path("/etc/buncker/config.json")).parent
        api_tokens = load_api_tokens(config_dir / "api-tokens.json")

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
        api_tokens=api_tokens,
        api_enabled=api_enabled,
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
            output_dir = (
                getattr(args, "output", None)
                or _resolve_transfer_path(config)
                or Path.cwd()
            )
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            out_file = output_dir / "buncker-request.json.enc"
            out_file.write_bytes(result)
            print(f"Transfer request saved to {out_file}")
        else:
            print(json.dumps(result, indent=2))

    elif args.command == "import":
        import_file = args.file
        if import_file is None:
            # Auto-scan transfer_path for newest *.tar.enc
            tp = _resolve_transfer_path(config)
            if tp is None:
                print(
                    "Error: no file specified and transfer_path not configured",
                    file=sys.stderr,
                )
                sys.exit(1)
            candidates = sorted(
                Path(tp).glob("*.tar.enc"), key=lambda p: p.stat().st_mtime
            )
            if not candidates:
                print(
                    f"Error: no *.tar.enc files found in {tp}",
                    file=sys.stderr,
                )
                sys.exit(1)
            import_file = candidates[-1]
            print(f"Auto-detected: {import_file}")

        file_data = import_file.read_bytes()
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
