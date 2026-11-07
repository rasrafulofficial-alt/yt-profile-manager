"""Encryption for everything stored on Google Drive.

The master password never leaves the PC. A 32-byte key is derived from it with
scrypt and a random salt (the salt is stored on Drive in vault.json, which is
not secret). All profile data is encrypted with AES-256-GCM before upload.
"""
import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_CHECK_PLAINTEXT = b"YTPM-VAULT-OK"
KEYRING_SERVICE = "YTProfileManager"


class WrongPassword(Exception):
    pass


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1)
    return kdf.derive(password.encode("utf-8"))


def encrypt(key: bytes, data: bytes) -> bytes:
    nonce = os.urandom(12)
    return b"YTPM1" + nonce + AESGCM(key).encrypt(nonce, data, None)


def decrypt(key: bytes, blob: bytes) -> bytes:
    if not blob.startswith(b"YTPM1"):
        raise ValueError("Not a YTPM encrypted file")
    nonce, ct = blob[5:17], blob[17:]
    try:
        return AESGCM(key).decrypt(nonce, ct, None)
    except Exception as e:
        raise WrongPassword("Could not decrypt (wrong password?)") from e


def new_vault(password: str) -> tuple[dict, bytes]:
    """Create vault.json content for a brand-new master password."""
    salt = os.urandom(16)
    key = derive_key(password, salt)
    vault = {
        "version": 1,
        "salt": base64.b64encode(salt).decode(),
        "check": base64.b64encode(encrypt(key, _CHECK_PLAINTEXT)).decode(),
    }
    return vault, key


def unlock_vault(vault: dict, password: str) -> bytes:
    salt = base64.b64decode(vault["salt"])
    key = derive_key(password, salt)
    check_key(vault, key)
    return key


def check_key(vault: dict, key: bytes) -> None:
    if decrypt(key, base64.b64decode(vault["check"])) != _CHECK_PLAINTEXT:
        raise WrongPassword("Wrong password")


# ---- Remember key on this PC (Windows Credential Manager via keyring) ----

def remember_key(account: str, key: bytes) -> None:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, account, base64.b64encode(key).decode())
    except Exception:
        pass


def recall_key(account: str) -> bytes | None:
    try:
        import keyring
        v = keyring.get_password(KEYRING_SERVICE, account)
        return base64.b64decode(v) if v else None
    except Exception:
        return None


def forget_key(account: str) -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, account)
    except Exception:
        pass


def dumps(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")
