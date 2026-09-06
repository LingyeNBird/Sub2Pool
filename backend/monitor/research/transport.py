"""Credential-free, signed, bounded HTTPS transport to the consented origin.

No redirects, ambient proxies/cookies, IP headers, User-Agent fingerprinting or
Sub2API credentials. Public DNS addresses are checked AND pinned for the socket;
TLS still verifies the original hostname. Network peers necessarily see an IP.
"""
import base64
import hashlib
import http.client
import ipaddress
import json
import secrets
import socket
import ssl
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings as django_settings
from ..secrets import decrypt_secret, encrypt_secret
from .protocol import PROTOCOL, STUDY, METHOD, canonical, method_digest


class DeliveryError(RuntimeError):
    pass


def normalize_endpoint(value):
    if value == "":
        return ""
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("接收网站地址无效")
    try:
        parts = urlsplit(value.strip())
        hostname = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
        local_test = getattr(django_settings, "RESEARCH_TEST_ALLOW_LOOPBACK", False)
        scheme_ok = parts.scheme == "https" or (local_test and parts.scheme == "http" and hostname in {"127.0.0.1", "localhost"})
        if not scheme_ok or not hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError
        if any(c in hostname for c in "\r\n /\\"):
            raise ValueError
        host = f"[{hostname}]" if ":" in hostname else hostname
        return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")
    except (ValueError, UnicodeError):
        raise ValueError("请填写 HTTPS 网站根地址，不能包含账号、路径、查询参数或片段") from None


def destination_ready(endpoint):
    if not endpoint:
        return False
    host = urlsplit(endpoint).hostname or ""
    return host != "invalid" and not host.endswith(".invalid")


IDENTITY_ERROR_MESSAGE = (
    "科研签名身份无法解密或已损坏；请恢复原 DJANGO_SECRET_KEY，"
    "或重新导入备份并授权以重置科研身份。旧贡献需在原实例撤回。"
)


def decode_identity_seed(encrypted):
    """Validate an existing identity; never silently rotate a contributor key."""
    try:
        if not isinstance(encrypted, str) or not encrypted:
            raise ValueError
        root = base64.b64decode(decrypt_secret(encrypted), validate=True)
        if len(root) != 32:
            raise ValueError
        return root
    except (ValueError, TypeError):
        # Includes malformed Fernet/base64 and UnicodeError. Never expose the
        # ciphertext or the lower-level exception through the API or logs.
        raise DeliveryError(IDENTITY_ERROR_MESSAGE) from None


def identity(config, endpoint):
    if not config.identity_encrypted:
        config.identity_encrypted = encrypt_secret(base64.b64encode(secrets.token_bytes(32)).decode())
    root = decode_identity_seed(config.identity_encrypted)
    # Different receiving origins/studies get unlinkable random public keys.
    seed = hashlib.sha256(root + b"\0" + endpoint.encode() + b"\0" + STUDY.encode()).digest()
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private, base64.b64encode(public).decode()


def packet(config, summary=None, *, endpoint=None, withdraw=False):
    endpoint = endpoint or config.endpoint
    private, public = identity(config, endpoint)
    payload = {"protocol": PROTOCOL, "study_id": STUDY, "method": METHOD,
               "method_digest": method_digest(), "public_key": public, "revision": config.report_revision}
    if not withdraw:
        payload["summary"] = summary
    path = "/api/v1/withdraw" if withdraw else "/api/v1/reports"
    body = canonical(payload)
    signature = base64.b64encode(private.sign(b"CodexSubscribeStudy/1\nPOST\n" + path.encode() + b"\n" + body)).decode()
    return path, body, signature


def send(endpoint, path, body, signature):
    endpoint = normalize_endpoint(endpoint)
    if not destination_ready(endpoint):
        raise DeliveryError("接收地址尚未配置，未发出网络请求")
    if len(body) > 32768:
        raise DeliveryError("统计报告超过安全大小限制")
    parts = urlsplit(endpoint)
    host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    connection = None
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        allowed = []
        for _, _, _, _, addr in addresses:
            ip = ipaddress.ip_address(addr[0])
            local = getattr(django_settings, "RESEARCH_TEST_ALLOW_LOOPBACK", False) and ip.is_loopback
            if not (ip.is_global or local):
                raise DeliveryError("接收网站必须解析为公网地址；未发送数据")
            allowed.append(addr[0])
        if not allowed:
            raise DeliveryError("无法解析接收网站")
        connection = http.client.HTTPConnection(host, port, timeout=10)
        sock = socket.create_connection((allowed[0], port), timeout=10)
        if parts.scheme == "https":
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            except Exception:
                sock.close()
                raise
        connection.sock = sock
        connection.request("POST", path, body=body, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "X-Study-Signature": signature,
        })
        response = connection.getresponse()
        # In particular 301/302/307/308 are errors; never follow to another site.
        if response.status not in (200, 201):
            raise DeliveryError(f"接收服务返回 HTTP {response.status}；本地事实未改变")
        raw = response.read(4097)
        if len(raw) > 4096:
            raise DeliveryError("接收服务响应超过限制")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("accepted") is not True:
            raise DeliveryError("接收服务未确认统计报告")
        return result
    except DeliveryError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        # Do not persist remote response bodies, URLs with credentials, raw
        # requests, proxy information, IP addresses or exception strings.
        raise DeliveryError("科研统计发送失败，请检查网络或接收服务；稍后自动重试") from None
    finally:
        if connection is not None:
            connection.close()
