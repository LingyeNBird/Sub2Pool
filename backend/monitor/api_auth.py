"""Authentication for the dedicated external API."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import AppSettings

API_KEY_PREFIX = "sub2pool_"


def hash_api_key(api_key: str) -> str:
    """Return the irreversible digest persisted in application settings."""

    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Generate a long prefixed key and its persisted metadata."""

    api_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(64)}"
    return api_key, hash_api_key(api_key), api_key[-4:]


class APIKeyPrincipal:
    """Administrator-issued principal with full access to exposed API operations."""

    is_authenticated = True
    is_active = True
    is_staff = True
    is_superuser = False
    username = "api-key"
    pk = None

    def __str__(self) -> str:
        return self.username


class APIKeyAuthentication(BaseAuthentication):
    """Authenticate the external API with an HTTP Bearer key."""

    keyword = b"bearer"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if parts[0].lower() != self.keyword:
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Authorization 请求头格式无效")
        try:
            api_key = parts[1].decode("utf-8")
        except UnicodeError as exc:
            raise AuthenticationFailed("API Key 格式无效") from exc

        config = AppSettings.load()
        if not config.readonly_api_key_hash or not hmac.compare_digest(
            hash_api_key(api_key),
            config.readonly_api_key_hash,
        ):
            raise AuthenticationFailed("API Key 无效或已失效")
        return APIKeyPrincipal(), {"hint": config.readonly_api_key_hint}

    def authenticate_header(self, _request) -> str:
        return "Bearer"
