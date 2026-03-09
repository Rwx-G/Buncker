"""CLI entry point for buncker-fetch."""

from __future__ import annotations

import argparse
import base64
import json
import logging
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
    pair_parser = sub.add_parser("pair", help="Enter 16-word mnemonic and derive keys")
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
        nargs="?",
        default=None,
        help="Path to .json.enc (auto-scans transfer_path if omitted)",
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
    fetch_parser.add_argument(
        "--deb",
        type=Path,
        default=None,
        help="Path to buncker .deb to include for offline auto-update",
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
    from shared.crypto import split_mnemonic

    print("Enter the 16-word mnemonic (space-separated):")
    mnemonic_input = input("> ").strip()

    words = mnemonic_input.split()
    if len(words) != 16:
        _print_error(
            f"Expected 16 words (12 secret + 4 salt), got {len(words)}.",
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

    # Extract salt from the last 4 words and derive keys
    try:
        mnemonic_12, salt = split_mnemonic(mnemonic_input)
    except Exception as exc:
        _print_error(str(exc), args)
        return 1

    aes_key, hmac_key = derive_keys(mnemonic_12, salt)

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

    # Resolve request file
    request_file = args.request_file
    if request_file is None:
        tp = config.get("transfer_path", "")
        if not tp:
            _print_error(
                "No request file specified and transfer_path not configured", args
            )
            return 1
        candidates = sorted(
            Path(tp).glob("*.json.enc"), key=lambda p: p.stat().st_mtime
        )
        if not candidates:
            _print_error(f"No *.json.enc files found in {tp}", args)
            return 1
        request_file = candidates[-1]
        if not getattr(args, "json_output", False):
            print(f"Auto-detected: {request_file}", file=sys.stderr)

    # Process request
    request_data = process_request(
        request_file,
        aes_key=aes_key,
        hmac_key=hmac_key,
    )

    blobs = request_data.get("blobs", [])
    images = request_data.get("images", [])
    source_id = request_data.get("source_id", "unknown")

    if not blobs and not images:
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

    # Fetch manifests for images listed in the request
    fetched_manifests = _fetch_manifests(request_data, config)

    # Extract blob digests from fetched manifests and download them too
    known_digests = {b.get("digest") for b in blobs}
    manifest_blobs: list[dict] = []
    for m in fetched_manifests:
        manifest = m.get("manifest", {})
        registry = m.get("registry", "docker.io")
        repository = m.get("repository", "")
        for layer in manifest.get("layers", []) + [manifest.get("config", {})]:
            digest = layer.get("digest", "")
            size = layer.get("size", 0)
            if digest and digest not in known_digests:
                known_digests.add(digest)
                manifest_blobs.append(
                    {
                        "digest": digest,
                        "size": size,
                        "registry": registry,
                        "repository": repository,
                    }
                )

    if manifest_blobs:
        by_registry_extra: dict[str, list[dict]] = {}
        for blob in manifest_blobs:
            reg = blob.get("registry", "docker.io")
            by_registry_extra.setdefault(reg, []).append(blob)
        for registry, registry_blobs in by_registry_extra.items():
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
        # Include manifest-derived blobs in response
        blobs = blobs + manifest_blobs

    # Build response - precedence: --output > transfer_path > cwd
    output_dir = args.output
    if output_dir is None:
        tp = config.get("transfer_path", "")
        output_dir = Path(tp) if tp else Path.cwd()
    # Resolve .deb for auto-update (FR15)
    deb_path = getattr(args, "deb", None)
    if deb_path and not deb_path.exists():
        _print_error(f".deb file not found: {deb_path}", args)
        return 1
    if deb_path and not getattr(args, "json_output", False):
        print(
            f"Including .deb for offline update: {deb_path.name}",
            file=sys.stderr,
        )

    response_path = build_response(
        cache,
        blobs,
        all_errors,
        aes_key=aes_key,
        hmac_key=hmac_key,
        source_id=source_id,
        output_dir=output_dir,
        deb_path=deb_path,
        manifests=fetched_manifests,
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


def _fetch_manifests(
    request_data: dict,
    config: dict,
) -> list[dict]:
    """Fetch OCI manifests for images listed in the transfer request.

    For each image, fetches the manifest index from the registry, resolves
    the platform-specific manifest, and returns both for inclusion in the
    response tar. The offline side caches these so future analyze calls
    can identify missing blobs without internet access.

    Args:
        request_data: Parsed transfer request with optional "images" list.
        config: buncker-fetch config dict.

    Returns:
        List of dicts with keys: registry, repository, tag, platform, manifest.
    """
    images = request_data.get("images", [])
    if not images:
        return []

    import hashlib

    _log = logging.getLogger("buncker.fetch.manifests")
    results = []

    for img in images:
        registry = img.get("registry", "docker.io")
        repository = img.get("repository", "")
        tag = img.get("tag", "latest")
        platform_str = img.get("platform", "linux/amd64")

        if not repository:
            continue

        host = registry
        if host == "docker.io":
            host = "registry-1.docker.io"

        try:
            credentials = load_credentials(config, registry)
            client = RegistryClient(host, credentials=credentials)

            # Fetch manifest (could be index or platform manifest)
            raw_manifest = client.fetch_manifest(repository, tag)
            media_type = raw_manifest.get("mediaType", "")

            # If it's a manifest list/index, find the target platform manifest
            is_index = "index" in media_type or "list" in media_type
            platform_manifest = None

            if is_index:
                platform_parts = platform_str.split("/")
                target_os = platform_parts[0] if platform_parts else "linux"
                target_arch = platform_parts[1] if len(platform_parts) > 1 else "amd64"

                for entry in raw_manifest.get("manifests", []):
                    p = entry.get("platform", {})
                    annotations = entry.get("annotations", {})
                    ref_type = annotations.get("vnd.docker.reference.type", "")
                    if ref_type == "attestation-manifest":
                        continue
                    os_match = p.get("os") == target_os
                    if os_match and p.get("architecture") == target_arch:
                        digest = entry["digest"]
                        platform_manifest = client.fetch_manifest(repository, digest)
                        break
            else:
                platform_manifest = raw_manifest

            if platform_manifest is None:
                _log.warning(
                    "manifest_platform_not_found",
                    extra={
                        "image": f"{repository}:{tag}",
                        "platform": platform_str,
                    },
                )
                continue

            # Add buncker metadata for caching
            raw = json.dumps(
                {k: v for k, v in platform_manifest.items() if k != "_buncker"},
                sort_keys=True,
            ).encode()
            source_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"

            # Check if upstream manifest changed since last fetch
            image_key = f"{registry}/{repository}:{tag}/{platform_str}"
            _check_manifest_changed(image_key, source_digest, _log)

            platform_manifest["_buncker"] = {
                "cached_at": datetime.now(tz=UTC).isoformat(),
                "source_digest": source_digest,
            }

            # Normalize platform string for filename (linux/amd64 -> linux-amd64)
            platform_file = platform_str.replace("/", "-")

            results.append(
                {
                    "registry": registry,
                    "repository": repository,
                    "tag": tag,
                    "platform": platform_file,
                    "manifest": platform_manifest,
                }
            )
            _log.info(
                "manifest_fetched",
                extra={"image": f"{repository}:{tag}", "platform": platform_str},
            )

        except Exception:
            _log.warning(
                "manifest_fetch_failed",
                extra={"image": f"{repository}:{tag}"},
                exc_info=True,
            )

    return results


_DIGEST_CACHE_PATH = _DEFAULT_CACHE_PATH / "manifest-digests.json"


def _load_digest_cache() -> dict[str, str]:
    """Load the manifest digest cache from disk."""
    if _DIGEST_CACHE_PATH.exists():
        try:
            return json.loads(_DIGEST_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_digest_cache(cache: dict[str, str]) -> None:
    """Save the manifest digest cache to disk."""
    _DIGEST_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DIGEST_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _check_manifest_changed(
    image_key: str,
    source_digest: str,
    log: logging.Logger,
) -> None:
    """Compare fresh manifest digest with cached one, warn if changed.

    Updates the digest cache with the new value regardless.

    Args:
        image_key: Image identifier (registry/repo:tag/platform).
        source_digest: SHA256 digest of the fresh manifest.
        log: Logger instance.
    """
    cache = _load_digest_cache()
    previous = cache.get(image_key)

    if previous and previous != source_digest:
        log.warning(
            "manifest_upstream_changed",
            extra={
                "image": image_key,
                "previous_digest": previous,
                "new_digest": source_digest,
            },
        )

    cache[image_key] = source_digest
    _save_digest_cache(cache)


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

    This prompts for the 16-word mnemonic, extracts the salt from the last
    4 words, and verifies against derived_key_check.
    """
    from shared.crypto import split_mnemonic

    print("Enter the 16-word mnemonic:", file=sys.stderr)
    mnemonic = input("> ").strip()

    try:
        mnemonic_12, salt = split_mnemonic(mnemonic)
    except Exception as exc:
        raise CryptoError(str(exc)) from exc

    aes_key, hmac_key = derive_keys(mnemonic_12, salt)

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
