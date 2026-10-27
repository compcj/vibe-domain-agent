import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet

from app.models import AuditEvent

password_hasher = PasswordHasher()
# Constant dummy hash equalizes Argon2 work for unknown users.
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(value: str) -> str:
    return password_hasher.hash(value)


def verify_password(encoded: str, value: str) -> bool:
    try:
        return password_hasher.verify(encoded, value)
    except VerificationError:
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(token: str, key: str) -> str:
    return hmac.new(key.encode(), token.encode(), hashlib.sha256).hexdigest()


def encrypt_secret(value: str, settings) -> str | None:
    if not value:
        return None
    if not settings.encryption_key:
        raise ValueError("ENCRYPTION_KEY must be configured")
    return Fernet(settings.encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt_secret(value: str | None, settings) -> str:
    if not value:
        return ""
    return Fernet(settings.encryption_key.encode()).decrypt(value.encode()).decode()


def audit(db, user, action: str, resource_type: str, resource_id=None):
    db.add(
        AuditEvent(
            actor_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )
