"""Cryptographic primitives for Buncker transfer security."""

import hashlib
import hmac as _hmac
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from shared.exceptions import CryptoError
from shared.wordlist import WORDLIST

# Re-export CryptoError for existing imports from shared.crypto
__all__ = [
    "CryptoError",
    "generate_mnemonic",
    "derive_keys",
    "encrypt",
    "decrypt",
    "sign",
    "verify",
]


def generate_mnemonic() -> str:
    """Generate a 12-word BIP-39 mnemonic from 128-bit entropy.

    Returns:
        Space-separated string of 12 words from the BIP-39 wordlist.
    """
    entropy = secrets.token_bytes(16)
    indices = _entropy_to_indices(entropy)
    return " ".join(WORDLIST[i] for i in indices)


def _entropy_to_indices(entropy: bytes) -> list[int]:
    """Convert 128-bit entropy to 12 word indices (11 bits each).

    BIP-39 process: append SHA256 checksum bits, then split into 11-bit groups.
    128 bits entropy + 4 bits checksum = 132 bits = 12 x 11-bit indices.
    """
    checksum = hashlib.sha256(entropy).digest()[0]
    bits = int.from_bytes(entropy, "big") << 4 | (checksum >> 4)
    mask = 0x7FF  # 11-bit mask
    return [(bits >> (11 * (11 - i))) & mask for i in range(12)]


def derive_keys(
    mnemonic: str,
    salt: bytes,
    iterations: int = 600_000,
) -> tuple[bytes, bytes]:
    """Derive AES and HMAC keys from a mnemonic via PBKDF2-SHA256.

    Args:
        mnemonic: Space-separated mnemonic string.
        salt: Random salt bytes.
        iterations: PBKDF2 iteration count (default 600,000).

    Returns:
        Tuple of (aes_key, hmac_key), each 32 bytes.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=64,
        salt=salt,
        iterations=iterations,
    )
    derived = kdf.derive(mnemonic.encode())
    return derived[:32], derived[32:]


def encrypt(data: bytes, aes_key: bytes) -> bytes:
    """Encrypt data with AES-256-GCM.

    Args:
        data: Plaintext bytes.
        aes_key: 32-byte AES key.

    Returns:
        nonce (12 bytes) + ciphertext + tag (16 bytes).
    """
    nonce = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return nonce + ciphertext


def decrypt(data: bytes, aes_key: bytes) -> bytes:
    """Decrypt AES-256-GCM data.

    Args:
        data: nonce (12 bytes) + ciphertext + tag.
        aes_key: 32-byte AES key.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        CryptoError: If decryption or authentication fails.
    """
    if len(data) < 12:
        raise CryptoError("Data too short for AES-256-GCM")
    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(aes_key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise CryptoError("Decryption failed", {"reason": str(exc)}) from exc


def sign(data: bytes, hmac_key: bytes) -> str:
    """Compute HMAC-SHA256 hex digest.

    Args:
        data: Data to sign.
        hmac_key: 32-byte HMAC key.

    Returns:
        Hex digest string.
    """
    return _hmac.new(hmac_key, data, hashlib.sha256).hexdigest()


def verify(data: bytes, hmac_key: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison.

    Args:
        data: Original data.
        hmac_key: 32-byte HMAC key.
        signature: Expected hex digest.

    Returns:
        True if signature is valid, False otherwise.
    """
    expected = sign(data, hmac_key)
    return _hmac.compare_digest(expected, signature)
