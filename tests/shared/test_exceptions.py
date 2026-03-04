"""Tests for shared.exceptions module."""

import pytest

from shared.exceptions import (
    BunckerError,
    ConfigError,
    CryptoError,
    RegistryError,
    ResolverError,
    StoreError,
    TransferError,
)

ALL_SUBCLASSES = [
    ConfigError,
    CryptoError,
    StoreError,
    ResolverError,
    RegistryError,
    TransferError,
]


class TestBunckerError:
    def test_message(self) -> None:
        err = BunckerError("something failed")
        assert err.message == "something failed"
        assert str(err) == "something failed"

    def test_context_default_empty(self) -> None:
        err = BunckerError("fail")
        assert err.context == {}

    def test_context_in_str(self) -> None:
        err = BunckerError("fail", {"key": "val"})
        assert "key" in str(err)
        assert "val" in str(err)

    def test_is_exception(self) -> None:
        with pytest.raises(BunckerError):
            raise BunckerError("test")


class TestSubclasses:
    @pytest.mark.parametrize("cls", ALL_SUBCLASSES)
    def test_inherits_from_buncker_error(self, cls: type) -> None:
        assert issubclass(cls, BunckerError)

    @pytest.mark.parametrize("cls", ALL_SUBCLASSES)
    def test_can_be_caught_by_parent(self, cls: type) -> None:
        with pytest.raises(BunckerError):
            raise cls("test error")

    @pytest.mark.parametrize("cls", ALL_SUBCLASSES)
    def test_message_and_context(self, cls: type) -> None:
        err = cls("msg", {"detail": 42})
        assert err.message == "msg"
        assert err.context == {"detail": 42}
        assert "42" in str(err)
