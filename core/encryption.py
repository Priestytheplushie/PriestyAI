import os
import base64
import logging
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet, InvalidToken
from config.settings import DISCORD_TOKEN, DATABASE_ENCRYPTION_KEY

logger = logging.getLogger("PriestyAI.Encryption")

SALT = b"priestyai_storage_salt_v1"

def _derive_fernet_key() -> bytes:
    if DATABASE_ENCRYPTION_KEY and len(DATABASE_ENCRYPTION_KEY) >= 32:
        try:
            base64.urlsafe_b64decode(DATABASE_ENCRYPTION_KEY.encode("utf-8"))
            return DATABASE_ENCRYPTION_KEY.encode("utf-8")
        except Exception:
            seed_material = DATABASE_ENCRYPTION_KEY.encode("utf-8")
    else:
        seed_material = (DISCORD_TOKEN or "priesty_default_fallback_seed").encode("utf-8")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SALT,
        iterations=100_000
    )
    derived = kdf.derive(seed_material)
    return base64.urlsafe_b64encode(derived)

class StorageEncryption:
    def __init__(self):
        try:
            self._key = _derive_fernet_key()
            self._fernet = Fernet(self._key)
            logger.info("[Encryption] Application-level field encryption engine initialized.")
        except Exception as e:
            logger.error(f"[Encryption] Failed to initialize Fernet cipher: {e}")
            self._fernet = None

    def encrypt_text(self, text: str) -> str:
        if not text or not self._fernet:
            return text

        try:
            encrypted_bytes = self._fernet.encrypt(text.encode("utf-8"))
            return encrypted_bytes.decode("utf-8")
        except Exception as e:
            logger.warning(f"[Encryption] Encryption failed: {e}")
            return text

    def decrypt_text(self, ciphertext: str) -> str:
        if not ciphertext or not self._fernet:
            return ciphertext

        if not ciphertext.startswith("gAAAAA"):
            return ciphertext

        try:
            decrypted_bytes = self._fernet.decrypt(ciphertext.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except (InvalidToken, Exception):
            return ciphertext

encryption_manager = StorageEncryption()