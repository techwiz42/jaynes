import pytest

from bicameral import auth


def test_a_password_is_stored_only_as_a_salted_hash_and_checked():
    stored = auth.hash_password("correct horse battery")
    assert "correct horse" not in stored and stored.startswith("scrypt$")
    assert auth.hash_password("correct horse battery") != stored            # a new salt each time
    assert auth.verify_password("correct horse battery", stored)
    assert not auth.verify_password("correct horse batterz", stored)


def test_an_unknown_user_or_a_damaged_hash_never_verifies():
    assert not auth.verify_password("anything at all", None)
    assert not auth.verify_password("anything at all", "garbage")
    assert not auth.verify_password("anything at all", "bcrypt$abc$def")


def test_emails_are_normalised_and_checked():
    assert auth.normalize_email("  Pat@Example.COM ") == "pat@example.com"
    for bad in ("", "no-at-sign", "a@b", "a b@example.com", "@example.com", "x" * 400 + "@example.com"):
        with pytest.raises(auth.AuthError):
            auth.normalize_email(bad)


def test_password_length_limits():
    auth.check_password("0123456789")
    for bad in ("short", "x" * 201, None):
        with pytest.raises(auth.AuthError):
            auth.check_password(bad)


def test_tokens_are_random_and_only_their_hash_is_kept():
    first, hashed = auth.new_token()
    second, _ = auth.new_token()
    assert first != second and len(first) >= 40 and hashed == auth.hash_token(first) and first not in hashed


def test_the_throttle_blocks_after_the_limit_and_forgets_after_the_window():
    now = {"t": 0.0}
    throttle = auth.Throttle(3, 60, clock=lambda: now["t"])
    for _ in range(3):
        assert not throttle.blocked("k")
        throttle.add("k")
    assert throttle.blocked("k") and not throttle.blocked("other")
    assert 1 <= throttle.retry_after("k") <= 60
    now["t"] = 61
    assert not throttle.blocked("k")
    throttle.add("k"); throttle.clear("k")
    assert not throttle.blocked("k") and throttle.retry_after("k") == 0
