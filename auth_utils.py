import hashlib
import hmac
import time


TOKEN_VERSION = "v1"


def _signature(expiry: int, password: str, secret: str) -> str:
    password_fingerprint = hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()
    message = (
        f"elkjop-lager-auth:{TOKEN_VERSION}:{expiry}:"
        f"{password_fingerprint}"
    ).encode("utf-8")

    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def create_remember_token(
    password: str,
    secret: str,
    lifetime_days: int,
    now: float | None = None,
) -> str:
    if not password or not secret:
        raise ValueError("Passord og signeringsnøkkel må være satt.")

    if lifetime_days < 1:
        raise ValueError("Levetiden må være minst én dag.")

    issued_at = time.time() if now is None else now
    expiry = int(issued_at + lifetime_days * 24 * 60 * 60)

    return f"{TOKEN_VERSION}.{expiry}.{_signature(expiry, password, secret)}"


def validate_remember_token(
    token: str | None,
    password: str,
    secret: str,
    now: float | None = None,
) -> bool:
    if not token or not password or not secret:
        return False

    try:
        version, expiry_text, supplied_signature = token.split(".", 2)
        expiry = int(expiry_text)
    except (AttributeError, TypeError, ValueError):
        return False

    if version != TOKEN_VERSION or len(supplied_signature) != 64:
        return False

    current_time = time.time() if now is None else now

    if expiry <= current_time:
        return False

    expected_signature = _signature(expiry, password, secret)

    return hmac.compare_digest(supplied_signature, expected_signature)
