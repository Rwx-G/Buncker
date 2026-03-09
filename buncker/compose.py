"""Docker Compose file parser - extracts image references from services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from shared.exceptions import ResolverError

_log = logging.getLogger("buncker.compose")


@dataclass
class ComposeService:
    """A service extracted from a Docker Compose file."""

    name: str
    image_ref: str | None
    dockerfile_path: Path | None
    build_context: Path | None


def parse_compose(
    path: Path,
    *,
    base_dir: Path | None = None,
) -> list[ComposeService]:
    """Parse a docker-compose.yml and extract image references.

    Args:
        path: Path to the Compose file.
        base_dir: Base directory for resolving relative paths.
            Defaults to the Compose file's parent directory.

    Returns:
        List of ComposeService entries for each service.

    Raises:
        ResolverError: If the file is invalid or missing required keys.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise ResolverError(
            f"Compose file not found: {path}",
            context={"path": str(path)},
        )

    base_dir = base_dir or path.parent

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ResolverError(
            f"Invalid YAML in {path.name}: {exc}",
            context={"path": str(path)},
        ) from exc

    if not isinstance(data, dict):
        raise ResolverError(
            f"Compose file must be a YAML mapping, got {type(data).__name__}",
            context={"path": str(path)},
        )

    services = data.get("services")
    if not services:
        raise ResolverError(
            "Compose file has no 'services' key or services is empty",
            context={"path": str(path)},
        )

    if not isinstance(services, dict):
        raise ResolverError(
            f"'services' must be a mapping, got {type(services).__name__}",
            context={"path": str(path)},
        )

    result: list[ComposeService] = []

    for name, svc in services.items():
        if not isinstance(svc, dict):
            _log.warning(
                "compose_service_skipped",
                extra={"service": name, "reason": "not a mapping"},
            )
            continue

        image_ref = svc.get("image")
        build = svc.get("build")
        dockerfile_path = None
        build_context = None

        if image_ref:
            # image: takes priority (AC3)
            result.append(
                ComposeService(
                    name=name,
                    image_ref=str(image_ref),
                    dockerfile_path=None,
                    build_context=None,
                )
            )
        elif build is not None:
            # build: section - extract dockerfile path
            if isinstance(build, str):
                # Short form: build: ./dir
                build_context = (base_dir / build).resolve()
                dockerfile_path = build_context / "Dockerfile"
            elif isinstance(build, dict):
                context_str = build.get("context", ".")
                build_context = (base_dir / context_str).resolve()
                dockerfile = build.get("dockerfile")
                if dockerfile:
                    # dockerfile can be relative to context or absolute
                    df_path = Path(dockerfile)
                    if df_path.is_absolute():
                        dockerfile_path = df_path
                    else:
                        dockerfile_path = (build_context / dockerfile).resolve()
                else:
                    # Default to Dockerfile in context (AC4)
                    dockerfile_path = build_context / "Dockerfile"
            else:
                _log.warning(
                    "compose_build_skipped",
                    extra={
                        "service": name,
                        "reason": (
                            "build must be string or mapping, "
                            f"got {type(build).__name__}"
                        ),
                    },
                )
                continue

            result.append(
                ComposeService(
                    name=name,
                    image_ref=None,
                    dockerfile_path=dockerfile_path,
                    build_context=build_context,
                )
            )
        else:
            # Neither image nor build (AC: skip with warning)
            _log.warning(
                "compose_service_skipped",
                extra={
                    "service": name,
                    "reason": "no image or build defined",
                },
            )

    return result


def parse_compose_content(content: str) -> list[ComposeService]:
    """Parse Compose YAML from a string (for remote API calls).

    Args:
        content: YAML string of the Compose file.

    Returns:
        List of ComposeService entries. Services with build paths
        will have unresolved paths (relative to unknown base).

    Raises:
        ResolverError: If the content is invalid YAML or missing required keys.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ResolverError(
            f"Invalid YAML in compose content: {exc}",
            context={},
        ) from exc

    if not isinstance(data, dict):
        raise ResolverError(
            f"Compose content must be a YAML mapping, got {type(data).__name__}",
            context={},
        )

    services = data.get("services")
    if not services:
        raise ResolverError(
            "Compose content has no 'services' key or services is empty",
            context={},
        )

    if not isinstance(services, dict):
        raise ResolverError(
            f"'services' must be a mapping, got {type(services).__name__}",
            context={},
        )

    result: list[ComposeService] = []

    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue

        image_ref = svc.get("image")
        build = svc.get("build")

        if image_ref:
            result.append(
                ComposeService(
                    name=name,
                    image_ref=str(image_ref),
                    dockerfile_path=None,
                    build_context=None,
                )
            )
        elif build is not None:
            # For remote content, we can only handle image refs from build
            # Dockerfile resolution requires local filesystem access
            _log.warning(
                "compose_build_remote_skipped",
                extra={
                    "service": name,
                    "reason": (
                        "build services require local filesystem"
                        " - use compose_path from localhost"
                    ),
                },
            )
        else:
            _log.warning(
                "compose_service_skipped",
                extra={"service": name, "reason": "no image or build defined"},
            )

    return result
