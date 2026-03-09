"""Dockerfile resolver - parses Dockerfiles and resolves image references."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from shared.exceptions import ResolverError

_log = logging.getLogger("buncker.resolver")

_DEFAULT_REGISTRY = "docker.io"
_DEFAULT_TAG = "latest"


@dataclass
class ResolvedImage:
    """A resolved base image reference from a Dockerfile FROM instruction."""

    raw: str
    resolved: str
    registry: str
    repository: str
    tag: str | None
    digest: str | None
    platform: str | None
    alias: str | None
    is_internal: bool
    is_private: bool
    line_number: int


@dataclass
class AnalysisResult:
    """Result of resolving a Dockerfile against the store and cache."""

    source_path: str
    build_args: dict[str, str]
    images: list[ResolvedImage]
    present_blobs: set[str]
    missing_blobs: list[dict] = field(default_factory=list)
    total_missing_size: int = 0
    warnings: list[str] = field(default_factory=list)


def resolve_dockerfile(
    path: Path,
    build_args: dict[str, str] | None = None,
    *,
    store: object,
    registry_client: object,
    private_registries: list[str] | None = None,
    default_platform: str = "linux/amd64",
) -> AnalysisResult:
    """Orchestrate: parse Dockerfile -> manifest lookup -> list_missing.

    Args:
        path: Path to the Dockerfile.
        build_args: Optional build-arg overrides.
        store: Store instance with ``list_missing()`` method.
        registry_client: ManifestCache with ``get_manifest()`` method.
        private_registries: Optional list of private registry patterns.
        default_platform: Default platform when FROM has none.

    Returns:
        AnalysisResult with resolved images and missing blob info.
    """
    build_args = build_args or {}
    images = parse_dockerfile(
        path,
        build_args,
        private_registries=private_registries,
    )

    result = AnalysisResult(
        source_path=str(path),
        build_args=build_args,
        images=images,
        present_blobs=set(),
    )

    seen_digests: set[str] = set()

    for image in images:
        if image.is_internal:
            continue

        if image.is_private:
            msg = f"Private registry {image.registry} skipped"
            result.warnings.append(msg)
            _log.warning(msg)
            continue

        if image.tag == "latest":
            msg = f"Image {image.resolved} uses tag 'latest' - consider pinning"
            result.warnings.append(msg)
            _log.warning(msg)

        platform = image.platform or default_platform
        reference = image.digest if image.digest else image.tag

        manifest = registry_client.get_manifest(
            image.registry,
            image.repository,
            reference,
            platform,
        )

        if manifest is None:
            msg = f"Manifest not cached for {image.resolved} - run fetch first"
            result.warnings.append(msg)
            _log.warning(msg)
            continue

        layer_digests = _extract_layer_digests(manifest)

        new_digests = [d for d in layer_digests if d not in seen_digests]
        seen_digests.update(new_digests)

        if not new_digests:
            continue

        missing = store.list_missing(new_digests)
        present = set(new_digests) - set(missing)
        result.present_blobs.update(present)

        for digest in missing:
            layer_info = _find_layer_info(manifest, digest)
            result.missing_blobs.append(
                {
                    "registry": image.registry,
                    "repository": image.repository,
                    "digest": digest,
                    "size": layer_info.get("size", 0),
                    "media_type": layer_info.get("mediaType", ""),
                }
            )
            result.total_missing_size += layer_info.get("size", 0)

    return result


def _extract_layer_digests(manifest: dict) -> list[str]:
    """Extract all layer digests from a manifest dict."""
    digests = []
    if "config" in manifest:
        digests.append(manifest["config"]["digest"])
    for layer in manifest.get("layers", []):
        digests.append(layer["digest"])
    return digests


def _find_layer_info(manifest: dict, digest: str) -> dict:
    """Find layer descriptor in manifest by digest."""
    if manifest.get("config", {}).get("digest") == digest:
        return manifest["config"]
    for layer in manifest.get("layers", []):
        if layer["digest"] == digest:
            return layer
    return {}


def parse_dockerfile(
    path: Path,
    build_args: dict[str, str] | None = None,
    *,
    private_registries: list[str] | None = None,
) -> list[ResolvedImage]:
    """Parse a Dockerfile and extract resolved base image references.

    Args:
        path: Path to the Dockerfile.
        build_args: Optional build-arg overrides.
        private_registries: Optional list of private registry patterns.

    Returns:
        List of ResolvedImage dataclasses for each FROM instruction.

    Raises:
        ResolverError: If an ARG variable is used but undefined.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise ResolverError(
            f"Dockerfile not found: {path}",
            context={"path": str(path)},
        )

    build_args = build_args or {}
    private_registries = private_registries or []

    text = path.read_text(encoding="utf-8")
    lines = _join_continuations(text)

    args: dict[str, str | None] = {}
    aliases: set[str] = set()
    images: list[ResolvedImage] = []
    seen_from = False

    for line_number, line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        upper = stripped.upper()

        if upper.startswith("ARG ") and not seen_from:
            _parse_arg(stripped, args, build_args)
            continue

        if upper.startswith("FROM "):
            seen_from = True
            image = _parse_from(
                stripped,
                line_number,
                args,
                build_args,
                aliases,
                private_registries,
            )
            if image is not None:
                images.append(image)
                if image.alias:
                    aliases.add(image.alias.lower())

    return images


def _join_continuations(text: str) -> list[tuple[int, str]]:
    """Join backslash-continuation lines and return (line_number, line)."""
    raw_lines = text.splitlines()
    result: list[tuple[int, str]] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        start = i + 1  # 1-based line number
        while line.rstrip().endswith("\\") and i + 1 < len(raw_lines):
            line = line.rstrip()[:-1] + " " + raw_lines[i + 1].lstrip()
            i += 1
        result.append((start, line))
        i += 1
    return result


def _parse_arg(
    line: str,
    args: dict[str, str | None],
    build_args: dict[str, str],
) -> None:
    """Parse a pre-FROM ARG instruction."""
    rest = line[4:].strip()
    if "=" in rest:
        key, _, value = rest.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        args[key] = build_args.get(key, value)
    else:
        key = rest.strip()
        args[key] = build_args.get(key)


def _parse_from(
    line: str,
    line_number: int,
    args: dict[str, str | None],
    build_args: dict[str, str],
    aliases: set[str],
    private_registries: list[str],
) -> ResolvedImage | None:
    """Parse a FROM instruction into a ResolvedImage."""
    rest = line[5:].strip()

    # Extract --platform flag
    platform = None
    platform_match = re.match(
        r"--platform=(\S+)\s+(.+)",
        rest,
        re.IGNORECASE,
    )
    if platform_match:
        platform = platform_match.group(1)
        rest = platform_match.group(2)

    # Substitute ARG variables
    platform = (
        _substitute_args(
            platform,
            args,
            build_args,
            line_number,
        )
        if platform
        else None
    )
    rest = _substitute_args(rest, args, build_args, line_number)

    # Extract alias
    alias = None
    as_match = re.match(r"(.+?)\s+[Aa][Ss]\s+(\S+)", rest)
    if as_match:
        rest = as_match.group(1).strip()
        alias = as_match.group(2)

    raw = rest

    # Handle scratch
    if raw.lower() == "scratch":
        return None

    # Check internal alias
    is_internal = raw.lower() in aliases

    # Parse image reference
    registry, repository, tag, digest = _parse_image_ref(raw)

    # Check private
    is_private = _is_private(registry, private_registries)

    resolved = _build_resolved(registry, repository, tag, digest)

    return ResolvedImage(
        raw=raw,
        resolved=resolved,
        registry=registry,
        repository=repository,
        tag=tag,
        digest=digest,
        platform=platform,
        alias=alias,
        is_internal=is_internal,
        is_private=is_private,
        line_number=line_number,
    )


def _substitute_args(
    text: str,
    args: dict[str, str | None],
    build_args: dict[str, str],
    line_number: int,
) -> str:
    """Replace $VAR, ${VAR}, and ${VAR:-default} references with ARG values.

    Supports Dockerfile variable substitution syntax:
    - ``$VAR`` and ``${VAR}`` - simple substitution
    - ``${VAR:-default}`` - use default if VAR is unset or empty
    - ``${VAR:+replacement}`` - use replacement if VAR is set and non-empty
    """

    def replacer(match: re.Match) -> str:
        # ${VAR:-default} or ${VAR:+replacement}
        if match.group(1) is not None:
            var = match.group(1)
            op = match.group(2)
            fallback = match.group(3)
            value = build_args.get(var, args.get(var))
            if op == ":-":
                if value is None or value == "":
                    return fallback
                return value
            if op == ":+":
                if value is not None and value != "":
                    return fallback
                return ""
            return value or fallback

        # ${VAR} simple form
        var = match.group(4) if match.group(4) is not None else match.group(5)

        if var in build_args:
            return build_args[var]
        if var in args:
            value = args[var]
            if value is None:
                raise ResolverError(
                    f"ARG '{var}' used but has no default and "
                    f"no build-arg override (line {line_number})",
                    context={"arg": var, "line": line_number},
                )
            return value
        raise ResolverError(
            f"ARG '{var}' used but not defined (line {line_number})",
            context={"arg": var, "line": line_number},
        )

    # Match ${VAR:-default}, ${VAR:+replacement}, ${VAR}, or $VAR
    return re.sub(
        r"\$\{(\w+)(:-|:\+)([^}]*)\}"  # ${VAR:-default} or ${VAR:+val}
        r"|\$\{(\w+)\}"  # ${VAR}
        r"|\$(\w+)",  # $VAR
        replacer,
        text,
    )


def _parse_image_ref(
    raw: str,
) -> tuple[str, str, str | None, str | None]:
    """Parse an image reference into (registry, repository, tag, digest).

    Handles all Docker reference formats:
    - ``nginx:1.25`` (bare name with tag)
    - ``myorg/myimage`` (org with default tag)
    - ``ghcr.io/owner/repo:v1`` (explicit registry with tag)
    - ``localhost:5000/myapp:v2`` (registry with port and tag)
    - ``nginx@sha256:abc...`` (digest reference)
    """
    digest = None
    tag = None

    # Extract digest first (mutually exclusive with tag)
    if "@sha256:" in raw:
        name, _, digest_part = raw.partition("@")
        digest = digest_part
        raw = name

    # Extract tag: find the last colon that is part of the tag
    # (not part of a registry:port). A tag colon appears after
    # the last slash, or in a bare name without slashes.
    if digest is None:
        last_slash = raw.rfind("/")
        last_colon = raw.rfind(":")
        if last_colon > last_slash:
            tag = raw[last_colon + 1 :]
            raw = raw[:last_colon]

    # Determine registry and repository
    registry, repository = _normalize_registry(raw)

    if tag is None and digest is None:
        tag = _DEFAULT_TAG

    return registry, repository, tag, digest


def _normalize_registry(name: str) -> tuple[str, str]:
    """Normalize Docker Hub short names to full registry/repository."""
    # Already has a dot or colon (explicit registry)
    parts = name.split("/", 1)
    if len(parts) == 1:
        # Bare name like "nginx"
        return _DEFAULT_REGISTRY, f"library/{name}"

    first = parts[0]
    if "." in first or ":" in first or first == "localhost":
        # Explicit registry
        return first, parts[1]

    # Docker Hub with org like "myorg/myimage"
    return _DEFAULT_REGISTRY, name


def _is_private(registry: str, patterns: list[str]) -> bool:
    """Check if a registry matches any private pattern."""
    for pattern in patterns:
        if pattern.endswith(":*"):
            if registry.startswith(pattern[:-2]):
                return True
        elif registry == pattern:
            return True
    return False


def _build_resolved(
    registry: str,
    repository: str,
    tag: str | None,
    digest: str | None,
) -> str:
    """Build the fully resolved image reference string."""
    base = f"{registry}/{repository}"
    if digest:
        return f"{base}@{digest}"
    if tag:
        return f"{base}:{tag}"
    return base
