"""Smoke tests for password hashing dependencies."""

from passlib.context import CryptContext


def test_bcrypt_hash_smoke():
    """Ensure the configured bcrypt backend can hash and verify passwords."""

    context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed = context.hash("example-password")
    assert context.verify("example-password", hashed)
