import json
import base64
import hashlib
import hmac
import secrets
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken

from .config import KEY_PATH


def _load_key() -> bytes:
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    KEY_PATH.chmod(0o600)
    return key


def encrypt_json(data: Dict[str, Any]) -> str:
    payload = json.dumps(data or {}, separators=(",", ":")).encode("utf-8")
    return Fernet(_load_key()).encrypt(payload).decode("utf-8")


def decrypt_json(token: str) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        payload = Fernet(_load_key()).decrypt(token.encode("utf-8"))
        return json.loads(payload.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError, ValueError):
        return {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False
