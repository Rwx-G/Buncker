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
    "split_mnemonic",
    "derive_keys",
    "encrypt",
    "decrypt",
    "sign",
    "verify",
]


def generate_mnemonic() -> str:
    """Generate a 16-word mnemonic: 12 BIP-39 words + 4 salt words.

    The first 12 words encode 128-bit entropy (BIP-39 standard).
    The last 4 words encode a 44-bit random salt used for PBKDF2 key
    derivation, so both sides derive the same keys from the mnemonic
    without needing a separate salt exchange.

    Returns:
        Space-separated string of 16 words from the BIP-39 wordlist.
    """
    entropy = secrets.token_bytes(16)
    mnemonic_indices = _entropy_to_indices(entropy)

    salt_bytes = secrets.token_bytes(6)  # 48 bits, we use 44
    salt_indices = _salt_to_indices(salt_bytes)

    all_indices = mnemonic_indices + salt_indices
    return " ".join(WORDLIST[i] for i in all_indices)


def _entropy_to_indices(entropy: bytes) -> list[int]:
    """Convert 128-bit entropy to 12 word indices (11 bits each).

    BIP-39 process: append SHA256 checksum bits, then split into 11-bit groups.
    128 bits entropy + 4 bits checksum = 132 bits = 12 x 11-bit indices.
    """
    checksum = hashlib.sha256(entropy).digest()[0]
    bits = int.from_bytes(entropy, "big") << 4 | (checksum >> 4)
    mask = 0x7FF  # 11-bit mask
    return [(bits >> (11 * (11 - i))) & mask for i in range(12)]


def _salt_to_indices(salt_bytes: bytes) -> list[int]:
    """Convert salt bytes to 4 word indices (11 bits each, 44 bits total)."""
    value = int.from_bytes(salt_bytes, "big")
    mask = 0x7FF  # 11-bit mask
    return [(value >> (11 * (3 - i))) & mask for i in range(4)]


def split_mnemonic(mnemonic: str) -> tuple[str, bytes]:
    """Split a 16-word mnemonic into the 12-word secret and salt bytes.

    Args:
        mnemonic: Space-separated 16-word mnemonic string.

    Returns:
        Tuple of (12-word mnemonic string, 32-byte salt derived from 4 salt words).
        The salt is expanded to 32 bytes via SHA-256 for PBKDF2 compatibility.

    Raises:
        CryptoError: If mnemonic does not have 16 words.
    """
    words = mnemonic.strip().split()
    if len(words) == 12:
        # Legacy 12-word mnemonic - no embedded salt
        raise CryptoError(
            "12-word mnemonic detected. Buncker now requires 16 words "
            "(12 secret + 4 salt). Please run buncker setup again."
        )
    if len(words) != 16:
        raise CryptoError(f"Expected 16-word mnemonic, got {len(words)} words")

    mnemonic_part = " ".join(words[:12])

    # Reconstruct salt from 4 word indices
    word_to_index = {w: i for i, w in enumerate(WORDLIST)}
    salt_indices = []
    for w in words[12:]:
        if w not in word_to_index:
            raise CryptoError(f"Unknown word in mnemonic: {w!r}")
        salt_indices.append(word_to_index[w])

    # Pack 4 x 11-bit indices into 44 bits
    value = 0
    for idx in salt_indices:
        value = (value << 11) | idx

    # Expand to 32 bytes via SHA-256 for PBKDF2 salt
    raw_salt = value.to_bytes(6, "big")
    salt = hashlib.sha256(raw_salt).digest()

    return mnemonic_part, salt


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
