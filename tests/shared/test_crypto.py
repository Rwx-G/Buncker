"""Comprehensive tests for shared.crypto module."""

import os
from pathlib import Path

import pytest

from shared.crypto import (
    CryptoError,
    decrypt,
    decrypt_env_value,
    derive_keys,
    encrypt,
    encrypt_env_value,
    generate_key_material,
    generate_mnemonic,
    sign,
    split_mnemonic,
    verify,
)
from shared.wordlist import WORDLIST


class TestGenerateMnemonic:
    """Tests for generate_mnemonic()."""

    def test_returns_16_words(self) -> None:
        mnemonic = generate_mnemonic()
        words = mnemonic.split()
        assert len(words) == 16

    def test_all_words_in_wordlist(self) -> None:
        mnemonic = generate_mnemonic()
        wordset = set(WORDLIST)
        for word in mnemonic.split():
            assert word in wordset

    def test_two_calls_differ(self) -> None:
        m1 = generate_mnemonic()
        m2 = generate_mnemonic()
        assert m1 != m2


class TestSplitMnemonic:
    """Tests for split_mnemonic()."""

    def test_splits_16_words_into_mnemonic_and_salt(self) -> None:
        full = generate_mnemonic()
        mnemonic_12, salt = split_mnemonic(full)
        assert len(mnemonic_12.split()) == 12
        assert len(salt) == 32

    def test_same_mnemonic_gives_same_salt(self) -> None:
        full = generate_mnemonic()
        _, salt1 = split_mnemonic(full)
        _, salt2 = split_mnemonic(full)
        assert salt1 == salt2

    def test_different_mnemonics_give_different_salts(self) -> None:
        m1 = generate_mnemonic()
        m2 = generate_mnemonic()
        _, salt1 = split_mnemonic(m1)
        _, salt2 = split_mnemonic(m2)
        assert salt1 != salt2

    def test_12_words_raises_error(self) -> None:
        words_12 = " ".join(WORDLIST[i] for i in range(12))
        with pytest.raises(CryptoError, match="16 words"):
            split_mnemonic(words_12)

    def test_wrong_count_raises_error(self) -> None:
        with pytest.raises(CryptoError, match="Expected 16-word"):
            split_mnemonic("one two three")

    def test_invalid_word_in_salt_raises(self) -> None:
        """Unknown word in the last 4 positions raises CryptoError."""
        # Build 16 words: 12 valid + 4 with one invalid
        valid_12 = " ".join(WORDLIST[i] for i in range(12))
        valid_salt = " ".join(WORDLIST[i] for i in range(12, 15))
        bad_mnemonic = f"{valid_12} {valid_salt} xyznotaword"
        with pytest.raises(CryptoError, match="Unknown word"):
            split_mnemonic(bad_mnemonic)

    def test_keys_derived_from_split_match(self) -> None:
        """Both sides derive same keys from the same 16-word mnemonic."""
        full = generate_mnemonic()
        mnemonic_12, salt = split_mnemonic(full)
        k1 = derive_keys(mnemonic_12, salt, iterations=1000)

        # Simulate the other side splitting the same mnemonic
        mnemonic_12b, salt_b = split_mnemonic(full)
        k2 = derive_keys(mnemonic_12b, salt_b, iterations=1000)

        assert k1 == k2


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


class TestEnvEncryption:
    """Tests for machine-id based env value encryption."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Encrypt then decrypt returns original value."""
        mid = tmp_path / "machine-id"
        mid.write_text("abcdef1234567890abcdef1234567890\n")
        mnemonic = "alpha bravo charlie delta echo foxtrot"
        encrypted = encrypt_env_value(
            mnemonic, machine_id_path=str(mid), key_material_path=None
        )
        decrypted = decrypt_env_value(
            encrypted, machine_id_path=str(mid), key_material_path=None
        )
        assert decrypted == mnemonic

    def test_different_machines_produce_different_ciphertext(
        self, tmp_path: Path
    ) -> None:
        mid1 = tmp_path / "mid1"
        mid1.write_text("aaaa1111aaaa1111aaaa1111aaaa1111\n")
        mid2 = tmp_path / "mid2"
        mid2.write_text("bbbb2222bbbb2222bbbb2222bbbb2222\n")
        value = "secret mnemonic"
        ct1 = encrypt_env_value(
            value, machine_id_path=str(mid1), key_material_path=None
        )
        ct2 = encrypt_env_value(
            value, machine_id_path=str(mid2), key_material_path=None
        )
        # Different machines -> different ciphertext
        assert ct1 != ct2

    def test_wrong_machine_fails_decrypt(self, tmp_path: Path) -> None:
        mid1 = tmp_path / "mid1"
        mid1.write_text("aaaa1111aaaa1111aaaa1111aaaa1111\n")
        mid2 = tmp_path / "mid2"
        mid2.write_text("bbbb2222bbbb2222bbbb2222bbbb2222\n")
        ct = encrypt_env_value(
            "secret", machine_id_path=str(mid1), key_material_path=None
        )
        with pytest.raises(CryptoError, match="Failed to decrypt"):
            decrypt_env_value(ct, machine_id_path=str(mid2), key_material_path=None)

    def test_missing_machine_id_raises(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "nonexistent")
        with pytest.raises(CryptoError, match="not found"):
            encrypt_env_value("test", machine_id_path=missing, key_material_path=None)

    def test_output_is_base64(self, tmp_path: Path) -> None:
        import base64

        mid = tmp_path / "machine-id"
        mid.write_text("abcdef1234567890abcdef1234567890\n")
        ct = encrypt_env_value(
            "hello", machine_id_path=str(mid), key_material_path=None
        )
        # Should be valid base64
        decoded = base64.b64decode(ct)
        assert len(decoded) > 0


class TestKeyMaterial:
    """Tests for root-only key-material hardening."""

    def test_generate_key_material_returns_32_bytes(self) -> None:
        km = generate_key_material()
        assert len(km) == 32
        assert isinstance(km, bytes)

    def test_generate_key_material_is_random(self) -> None:
        km1 = generate_key_material()
        km2 = generate_key_material()
        assert km1 != km2

    def test_roundtrip_with_key_material(self, tmp_path: Path) -> None:
        """Encrypt/decrypt with key-material file present."""
        mid = tmp_path / "machine-id"
        mid.write_text("abcdef1234567890abcdef1234567890\n")
        km = tmp_path / "key-material"
        km.write_bytes(generate_key_material())

        value = "secret mnemonic phrase"
        ct = encrypt_env_value(
            value, machine_id_path=str(mid), key_material_path=str(km)
        )
        pt = decrypt_env_value(ct, machine_id_path=str(mid), key_material_path=str(km))
        assert pt == value

    def test_key_material_changes_derived_key(self, tmp_path: Path) -> None:
        """Same machine-id with different key-material produces different ciphertext."""
        mid = tmp_path / "machine-id"
        mid.write_text("abcdef1234567890abcdef1234567890\n")

        # Without key-material
        encrypt_env_value("secret", machine_id_path=str(mid), key_material_path=None)

        # With key-material
        km = tmp_path / "key-material"
        km.write_bytes(generate_key_material())
        ct_with_km = encrypt_env_value(
            "secret", machine_id_path=str(mid), key_material_path=str(km)
        )

        # Cannot decrypt without matching key-material
        with pytest.raises(CryptoError, match="Failed to decrypt"):
            decrypt_env_value(
                ct_with_km, machine_id_path=str(mid), key_material_path=None
            )

    def test_wrong_key_material_fails(self, tmp_path: Path) -> None:
        """Different key-material file fails decryption."""
        mid = tmp_path / "machine-id"
        mid.write_text("abcdef1234567890abcdef1234567890\n")
        km1 = tmp_path / "km1"
        km1.write_bytes(generate_key_material())
        km2 = tmp_path / "km2"
        km2.write_bytes(generate_key_material())

        ct = encrypt_env_value(
            "secret", machine_id_path=str(mid), key_material_path=str(km1)
        )
        with pytest.raises(CryptoError, match="Failed to decrypt"):
            decrypt_env_value(ct, machine_id_path=str(mid), key_material_path=str(km2))

    def test_missing_key_material_falls_back(self, tmp_path: Path) -> None:
        """When key-material file does not exist, uses machine-id only."""
        mid = tmp_path / "machine-id"
        mid.write_text("abcdef1234567890abcdef1234567890\n")
        missing_km = str(tmp_path / "nonexistent-km")

        # Should not raise - falls back to machine-id only
        ct = encrypt_env_value(
            "secret", machine_id_path=str(mid), key_material_path=missing_km
        )
        pt = decrypt_env_value(
            ct, machine_id_path=str(mid), key_material_path=missing_km
        )
        assert pt == "secret"


class TestCryptoError:
    """Tests for CryptoError exception."""

    def test_message(self) -> None:
        err = CryptoError("test error")
        assert str(err) == "test error"
        assert err.context == {}

    def test_context(self) -> None:
        err = CryptoError("fail", {"key": "value"})
        assert err.context == {"key": "value"}
