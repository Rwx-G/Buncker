"""CLI entry point for buncker-fetch."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from buncker_fetch.cache import Cache
from buncker_fetch.config import (
    _DEFAULT_CONFIG_PATH,
    load_config,
    save_config,
    validate_config,
)
from buncker_fetch.fetcher import Fetcher
from buncker_fetch.registry_client import RegistryClient, load_credentials
from buncker_fetch.transfer import build_response, process_request
from shared.crypto import decrypt, derive_keys, encrypt
from shared.exceptions import BunckerError, CryptoError
from shared.wordlist import WORDLIST

_DEFAULT_CACHE_PATH = Path.home() / ".buncker" / "cache"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    try:
        return args.func(args)
    except BunckerError as exc:
        _print_error(str(exc), args)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="buncker-fetch",
        description="Online fetch tool for Buncker air-gapped Docker sync",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config file (default: ~/.buncker/config.json)",
    )

    sub = parser.add_subparsers(dest="command")

    # pair
    pair_parser = sub.add_parser("pair", help="Enter 12-word mnemonic and derive keys")
    pair_parser.set_defaults(func=cmd_pair)

    # inspect
    inspect_parser = sub.add_parser(
        "inspect",
        help="Decrypt and display request summary",
    )
    inspect_parser.add_argument(
        "request_file",
        type=Path,
        help="Path to .json.enc",
    )
    inspect_parser.set_defaults(func=cmd_inspect)

    # fetch
    fetch_parser = sub.add_parser("fetch", help="Full fetch cycle")
    fetch_parser.add_argument(
        "request_file",
        type=Path,
        help="Path to .json.enc",
    )
    fetch_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory for response",
    )
    fetch_parser.add_argument(
        "--parallelism",
        type=int,
        default=4,
        help="Number of parallel downloads",
    )
    fetch_parser.set_defaults(func=cmd_fetch)

    # status
    status_parser = sub.add_parser("status", help="Display cache stats")
    status_parser.set_defaults(func=cmd_status)

    # cache
    cache_parser = sub.add_parser("cache", help="Cache management")
    cache_sub = cache_parser.add_subparsers(dest="cache_command")
    clean_parser = cache_sub.add_parser("clean", help="Clean old cached blobs")
    clean_parser.add_argument(
        "--older-than",
        type=str,
        default="30d",
        help="Delete blobs older than Nd (e.g., 30d)",
    )
    clean_parser.set_defaults(func=cmd_cache_clean)
    cache_parser.set_defaults(func=lambda a: cache_parser.print_help() or 1)

    return parser


def cmd_pair(args: argparse.Namespace) -> int:
    """Handle 'pair' subcommand."""
    print("Enter the 12-word mnemonic (space-separated):")
    mnemonic_input = input("> ").strip()

    words = mnemonic_input.split()
    if len(words) != 12:
        _print_error(
            f"Expected 12 words, got {len(words)}. Check your input.",
            args,
        )
        return 1

    # Validate words
    for word in words:
        if word not in WORDLIST:
            _print_error(
                f"Word '{word}' not in BIP-39 wordlist. Check spelling.",
                args,
            )
            return 1

    # Generate salt and derive keys
    salt = os.urandom(32)
    aes_key, hmac_key = derive_keys(mnemonic_input, salt)

    # Create derived_key_check (encrypt a known marker)
    marker = b"buncker-pair-check"
    derived_key_check = base64.b64encode(encrypt(marker, aes_key)).decode()

    # Load existing config or create new
    config_path = args.config or _DEFAULT_CONFIG_PATH
    config = load_config(config_path)
    config["salt"] = base64.b64encode(salt).decode()
    config["derived_key_check"] = derived_key_check
    save_config(config, config_path)

    _print_output({"status": "success", "message": "Pairing successful"}, args)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Handle 'inspect' subcommand."""
    config_path = args.config or _DEFAULT_CONFIG_PATH
    config = load_config(config_path)
    validate_config(config)

    aes_key, hmac_key = _derive_keys_from_config(config)

    request_data = process_request(
        args.request_file,
        aes_key=aes_key,
        hmac_key=hmac_key,
    )

    blobs = request_data.get("blobs", [])
    registries = set()
    total_size = 0
    for blob in blobs:
        registries.add(blob.get("registry", "unknown"))
        total_size += blob.get("size", 0)

    summary = {
        "source_id": request_data.get("source_id"),
        "generated_at": request_data.get("generated_at"),
        "buncker_version": request_data.get("buncker_version"),
        "blob_count": len(blobs),
        "total_size": total_size,
        "registries": sorted(registries),
    }

    _print_output(summary, args)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Handle 'fetch' subcommand."""
    config_path = args.config or _DEFAULT_CONFIG_PATH
    config = load_config(config_path)
    validate_config(config)

    aes_key, hmac_key = _derive_keys_from_config(config)

    # Process request
    request_data = process_request(
        args.request_file,
        aes_key=aes_key,
        hmac_key=hmac_key,
    )

    blobs = request_data.get("blobs", [])
    source_id = request_data.get("source_id", "unknown")

    if not blobs:
        _print_output({"status": "success", "message": "No blobs to fetch"}, args)
        return 0

    # Group blobs by registry
    by_registry: dict[str, list[dict]] = {}
    for blob in blobs:
        reg = blob.get("registry", "docker.io")
        by_registry.setdefault(reg, []).append(blob)

    cache = Cache(_DEFAULT_CACHE_PATH)
    all_errors: list[dict] = []
    all_downloaded: list[str] = []
    all_skipped: list[str] = []

    for registry, registry_blobs in by_registry.items():
        # Normalize docker.io -> registry-1.docker.io
        host = registry
        if host == "docker.io":
            host = "registry-1.docker.io"

        credentials = load_credentials(config, registry)
        client = RegistryClient(host, credentials=credentials)
        fetcher = Fetcher(
            client,
            cache,
            parallelism=args.parallelism,
            progress_json=args.json_output,
        )
        result = fetcher.fetch(registry_blobs)
        all_downloaded.extend(result.downloaded)
        all_skipped.extend(result.skipped)
        all_errors.extend(result.errors)

    # Build response
    output_dir = args.output or Path.cwd()
    response_path = build_response(
        cache,
        blobs,
        all_errors,
        aes_key=aes_key,
        hmac_key=hmac_key,
        source_id=source_id,
        output_dir=output_dir,
    )

    summary = {
        "status": "success",
        "downloaded": len(all_downloaded),
        "skipped": len(all_skipped),
        "errors": len(all_errors),
        "response_file": str(response_path),
    }
    _print_output(summary, args)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Handle 'status' subcommand."""
    cache = Cache(_DEFAULT_CACHE_PATH)
    stats = cache.stats()

    if stats["oldest"] is not None:
        stats["oldest"] = datetime.fromtimestamp(stats["oldest"], tz=UTC).isoformat()
    if stats["newest"] is not None:
        stats["newest"] = datetime.fromtimestamp(stats["newest"], tz=UTC).isoformat()

    _print_output(stats, args)
    return 0


def cmd_cache_clean(args: argparse.Namespace) -> int:
    """Handle 'cache clean' subcommand."""
    older_than = args.older_than
    if not older_than.endswith("d"):
        _print_error("--older-than must be in format Nd (e.g., 30d)", args)
        return 1

    try:
        days = int(older_than[:-1])
    except ValueError:
        _print_error(f"Invalid number in --older-than: {older_than}", args)
        return 1

    cache = Cache(_DEFAULT_CACHE_PATH)
    result = cache.cache_clean(older_than_days=days)

    _print_output(result, args)
    return 0


def _derive_keys_from_config(config: dict) -> tuple[bytes, bytes]:
    """Derive AES and HMAC keys from config (requires user mnemonic input).

    This prompts for the mnemonic and verifies it against derived_key_check.
    """
    salt = base64.b64decode(config["salt"])

    print("Enter the 12-word mnemonic:", file=sys.stderr)
    mnemonic = input("> ").strip()

    aes_key, hmac_key = derive_keys(mnemonic, salt)

    # Verify against derived_key_check
    check_data = base64.b64decode(config["derived_key_check"])
    try:
        decrypt(check_data, aes_key)
    except Exception as exc:
        raise CryptoError(
            "Mnemonic verification failed. Wrong mnemonic or corrupted config.",
        ) from exc

    return aes_key, hmac_key


def _print_output(data: dict, args: argparse.Namespace) -> None:
    """Print output in human-readable or JSON format."""
    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            print(f"  {key}: {value}")


def _print_error(message: str, args: argparse.Namespace) -> None:
    """Print error message."""
    if getattr(args, "json_output", False):
        print(json.dumps({"error": message}), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
