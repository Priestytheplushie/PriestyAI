import os
import sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

GITHUB_APP_DIR = Path(__file__).parent.parent.resolve()
if str(GITHUB_APP_DIR) not in sys.path:
    sys.path.insert(0, str(GITHUB_APP_DIR))


_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_pem_str = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")

os.environ.setdefault("GITHUB_TOKEN", "mock_github_pat_12345")
os.environ.setdefault("GITHUB_APP_ID", "123456")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test_webhook_secret_key")
os.environ.setdefault("GEMINI_API_KEY", "mock_gemini_key_1")

from app.config import settings

type(settings).private_key_pem = property(lambda self: _pem_str)
