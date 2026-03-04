"""Comprehensive tests for shared.crypto module."""

import os

import pytest

from shared.crypto import (
    CryptoError,
    decrypt,
    derive_keys,
    encrypt,
    generate_mnemonic,
    sign,
    verify,
)
from shared.wordlist import WORDLIST


class TestGenerateMnemonic:
    """Tests for generate_mnemonic()."""

    def test_returns_12_words(self) -> None:
        mnemonic = generate_mnemonic()
        words = mnemonic.split()
        assert len(words) == 12

    def test_all_words_in_wordlist(self) -> None:
        mnemonic = generate_mnemonic()
        wordset = set(WORDLIST)
        for word in mnemonic.split():
            assert word in wordset

    def test_two_calls_differ(self) -> None:
        m1 = generate_mnemonic()
        m2 = generate_mnemonic()
        assert m1 != m2


class TestDeriveKeys:
    """Tests for derive_keys()."""

    def test_deterministic(self) -> None:
        salt = b"fixed_salt"
        k1 = derive_keys("test mnemonic", salt, iterations=1000)
        k2 = derive_keys("test mnemonic", salt, iterations=1000)
        assert k1 == k2

    def test_returns_32_byte_keys(self) -> None:
        aes_key, hmac_key = derive_keys("test", b"salt", iterations=1000)
        assert len(aes_key) == 32
        assert len(hmac_key) == 32

    def test_different_mnemonics_differ(self) -> None:
        salt = b"salt"
        k1 = derive_keys("mnemonic one", salt, iterations=1000)
        k2 = derive_keys("mnemonic two", salt, iterations=1000)
        assert k1 != k2

    def test_different_salts_differ(self) -> None:
        k1 = derive_keys("same", b"salt_a", iterations=1000)
        k2 = derive_keys("same", b"salt_b", iterations=1000)
        assert k1 != k2


class TestEncryptDecrypt:
    """Tests for encrypt() and decrypt()."""

    @pytest.fixture()
    def aes_key(self) -> bytes:
        return os.urandom(32)

    def test_round_trip_small(self, aes_key: bytes) -> None:
        plaintext = b"hello world"
        ciphertext = encrypt(plaintext, aes_key)
        assert decrypt(ciphertext, aes_key) == plaintext

    def test_round_trip_empty(self, aes_key: bytes) -> None:
        plaintext = b""
        ciphertext = encrypt(plaintext, aes_key)
        assert decrypt(ciphertext, aes_key) == plaintext

    def test_round_trip_large(self, aes_key: bytes) -> None:
        plaintext = os.urandom(1_000_000)
        ciphertext = encrypt(plaintext, aes_key)
        assert decrypt(ciphertext, aes_key) == plaintext

    def test_ciphertext_longer_than_plaintext(self, aes_key: bytes) -> None:
        plaintext = b"data"
        ciphertext = encrypt(plaintext, aes_key)
        # nonce (12) + plaintext + tag (16) = at least 28 bytes more
        assert len(ciphertext) > len(plaintext)

    def test_wrong_key_raises_crypto_error(self, aes_key: bytes) -> None:
        ciphertext = encrypt(b"secret", aes_key)
        wrong_key = os.urandom(32)
        with pytest.raises(CryptoError):
            decrypt(ciphertext, wrong_key)

    def test_tampered_ciphertext_raises_crypto_error(self, aes_key: bytes) -> None:
        ciphertext = encrypt(b"secret", aes_key)
        tampered = bytearray(ciphertext)
        tampered[-1] ^= 0xFF
        with pytest.raises(CryptoError):
            decrypt(bytes(tampered), aes_key)

    def test_data_too_short_raises_crypto_error(self, aes_key: bytes) -> None:
        with pytest.raises(CryptoError, match="too short"):
            decrypt(b"short", aes_key)


class TestSignVerify:
    """Tests for sign() and verify()."""

    @pytest.fixture()
    def hmac_key(self) -> bytes:
        return os.urandom(32)

    def test_sign_returns_hex_string(self, hmac_key: bytes) -> None:
        sig = sign(b"data", hmac_key)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex digest is 64 chars
        int(sig, 16)  # must be valid hex

    def test_verify_valid_signature(self, hmac_key: bytes) -> None:
        data = b"important data"
        sig = sign(data, hmac_key)
        assert verify(data, hmac_key, sig) is True

    def test_verify_wrong_signature(self, hmac_key: bytes) -> None:
        sign(b"data", hmac_key)
        assert verify(b"data", hmac_key, "0" * 64) is False

    def test_verify_wrong_key(self, hmac_key: bytes) -> None:
        data = b"data"
        sig = sign(data, hmac_key)
        wrong_key = os.urandom(32)
        assert verify(data, wrong_key, sig) is False

    def test_verify_wrong_data(self, hmac_key: bytes) -> None:
        sig = sign(b"original", hmac_key)
        assert verify(b"tampered", hmac_key, sig) is False


class TestCryptoError:
    """Tests for CryptoError exception."""

    def test_message(self) -> None:
        err = CryptoError("test error")
        assert str(err) == "test error"
        assert err.context == {}

    def test_context(self) -> None:
        err = CryptoError("fail", {"key": "value"})
        assert err.context == {"key": "value"}
