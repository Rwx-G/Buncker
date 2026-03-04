"""Buncker exception hierarchy."""


class BunckerError(Exception):
    """Base exception for all Buncker errors.

    All errors must be actionable: what failed + context + what to do.
    """

    def __init__(self, message: str, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:
        if self.context:
            return f"{self.message} ({self.context})"
        return self.message


class ConfigError(BunckerError):
    """Configuration-related errors."""


class CryptoError(BunckerError):
    """Cryptographic operation failures."""


class StoreError(BunckerError):
    """Blob store operation failures."""


class ResolverError(BunckerError):
    """Dockerfile resolver failures."""


class RegistryError(BunckerError):
    """OCI registry communication failures."""


class TransferError(BunckerError):
    """Transfer manifest/import failures."""
