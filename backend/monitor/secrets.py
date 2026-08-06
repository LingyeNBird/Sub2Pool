"""敏感配置的可逆加密。

Fernet 密钥由 Django SECRET_KEY 派生。备份数据库时也必须备份 SECRET_KEY；更换该值前应先重新录入页面中的密钥。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("敏感配置无法解密，请确认 DJANGO_SECRET_KEY 未发生变化") from exc
