import pytest

from bicameral import auth


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch):
    """scrypt is made deliberately slow; the tests do not need it to be."""
    monkeypatch.setattr(auth, "SCRYPT_N", 2 ** 4)
