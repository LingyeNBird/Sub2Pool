"""Minimal RESP transport for CLIProxyAPI's built-in usage stream."""

from __future__ import annotations

import json
import socket
import ssl
import time
from typing import Any, Callable
from urllib.parse import urlparse

from ...models import AppSettings
from ...secrets import decrypt_secret
from .client import CPAError

_MAX_RESP_BULK_BYTES = 8 * 1024 * 1024
_MAX_RESP_ARRAY_ITEMS = 1024
_MAX_RESP_DEPTH = 8
_MAX_RESP_LINE_BYTES = 128


class _RESPNeedMoreData(Exception):
    pass


def _parse_line(buffer: bytearray, offset: int) -> tuple[bytes, int]:
    ending = buffer.find(b"\r\n", offset)
    if ending < 0:
        if len(buffer) - offset > _MAX_RESP_LINE_BYTES:
            raise CPAError("CPA usage stream returned an oversized RESP line")
        raise _RESPNeedMoreData
    if ending - offset > _MAX_RESP_LINE_BYTES:
        raise CPAError("CPA usage stream returned an oversized RESP line")
    return bytes(buffer[offset:ending]), ending + 2


def _parse_response(
    buffer: bytearray,
    offset: int = 0,
    depth: int = 0,
) -> tuple[Any, int]:
    if depth > _MAX_RESP_DEPTH:
        raise CPAError("CPA usage stream returned overly nested RESP data")
    if offset >= len(buffer):
        raise _RESPNeedMoreData
    prefix = chr(buffer[offset])
    offset += 1
    if prefix in {"+", "-", ":", "$", "*"}:
        line, offset = _parse_line(buffer, offset)
    else:
        raise CPAError("CPA usage stream returned an unsupported RESP frame")

    if prefix == "+":
        return line.decode("utf-8", errors="replace"), offset
    if prefix == "-":
        message = line.decode("utf-8", errors="replace")
        raise CPAError(f"CPA usage stream rejected the request: {message}")
    if prefix == ":":
        try:
            return int(line), offset
        except ValueError as exc:
            raise CPAError("CPA usage stream returned an invalid RESP integer") from exc
    try:
        length = int(line)
    except ValueError as exc:
        raise CPAError("CPA usage stream returned an invalid RESP length") from exc
    if prefix == "$":
        if length < 0:
            return None, offset
        if length > _MAX_RESP_BULK_BYTES:
            raise CPAError("CPA usage stream returned an oversized RESP payload")
        ending = offset + length
        if len(buffer) < ending + 2:
            raise _RESPNeedMoreData
        if buffer[ending : ending + 2] != b"\r\n":
            raise CPAError("CPA usage stream returned malformed RESP data")
        value = bytes(buffer[offset:ending]).decode("utf-8", errors="replace")
        return value, ending + 2
    if length < 0:
        return None, offset
    if length > _MAX_RESP_ARRAY_ITEMS:
        raise CPAError("CPA usage stream returned an oversized RESP array")
    values: list[Any] = []
    for _index in range(length):
        value, offset = _parse_response(buffer, offset, depth + 1)
        values.append(value)
    return values, offset


class _RESPConnection:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = bytearray()

    def send_command(self, *parts: str) -> None:
        payload = bytearray(f"*{len(parts)}\r\n".encode("ascii"))
        for part in parts:
            value = part.encode("utf-8")
            payload.extend(f"${len(value)}\r\n".encode("ascii"))
            payload.extend(value)
            payload.extend(b"\r\n")
        self.sock.sendall(payload)

    def _read_more(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("CPA closed the usage subscription")
        self.buffer.extend(chunk)
        if len(self.buffer) > _MAX_RESP_BULK_BYTES + 4096:
            raise CPAError("CPA usage stream exceeded the receive buffer limit")

    def read_response(self) -> Any:
        while True:
            try:
                value, consumed = _parse_response(self.buffer)
            except _RESPNeedMoreData:
                self._read_more()
                continue
            del self.buffer[:consumed]
            return value


def decode_usage_message(frame: Any) -> dict[str, Any] | None:
    if not isinstance(frame, list) or len(frame) != 3:
        return None
    if str(frame[0]).lower() != "message" or str(frame[1]).lower() != "usage":
        return None
    payload = frame[2]
    if not isinstance(payload, str):
        return None
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CPAError("CPA usage stream returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        return None
    if set(decoded) <= {"support_refresh", "refresh"}:
        return None
    return decoded


class CPAUsageSubscriber:
    """Authenticate and subscribe to CPA's native ``usage`` RESP channel."""

    def __init__(
        self,
        config: AppSettings,
        *,
        base_url: str | None = None,
        management_key: str | None = None,
        request_timeout_seconds: int | None = None,
        verify_tls: bool | None = None,
    ):
        self.base_url = base_url or config.cpa_base_url
        self.management_key = management_key or decrypt_secret(
            config.cpa_management_key_encrypted
        )
        self.timeout = request_timeout_seconds or config.request_timeout_seconds
        self.verify_tls = config.verify_tls if verify_tls is None else verify_tls
        self.sock: socket.socket | None = None
        self.connection: _RESPConnection | None = None

    def __enter__(self) -> "CPAUsageSubscriber":
        self.connect()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _open_authenticated(self) -> tuple[socket.socket, _RESPConnection]:
        if not self.management_key:
            raise CPAError("尚未配置 CPA Management Key")
        parsed = urlparse(self.base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CPAError("CPA 地址必须是有效的 HTTP 或 HTTPS URL")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection(
                (parsed.hostname, port),
                timeout=self.timeout,
            )
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                if not self.verify_tls:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=parsed.hostname)
            sock.settimeout(self.timeout)
            connection = _RESPConnection(sock)
            connection.send_command("AUTH", self.management_key)
            if str(connection.read_response()).upper() != "OK":
                raise CPAError("CPA usage stream authentication failed")
            return sock, connection
        except CPAError:
            if sock is not None:
                sock.close()
            raise
        except (OSError, ValueError) as exc:
            if sock is not None:
                sock.close()
            raise CPAError(
                f"无法连接 CPA usage stream：{exc.__class__.__name__}"
            ) from exc

    def probe(self) -> dict[str, str]:
        """Verify the exact RESP transport and AUTH path without subscribing."""

        sock, _connection = self._open_authenticated()
        sock.close()
        return {"resp_transport": "ok", "resp_auth": "ok"}

    def connect(self) -> None:
        sock, connection = self._open_authenticated()
        try:
            connection.send_command("SUBSCRIBE", "usage")
            acknowledgement = connection.read_response()
            if (
                not isinstance(acknowledgement, list)
                or len(acknowledgement) != 3
                or str(acknowledgement[0]).lower() != "subscribe"
                or str(acknowledgement[1]).lower() != "usage"
                or acknowledgement[2] != 1
            ):
                raise CPAError("CPA usage stream subscription failed")
        except Exception:
            sock.close()
            raise
        self.sock = sock
        self.connection = connection

    def unsubscribe(
        self,
        on_record: Callable[[dict[str, Any]], None],
        *,
        timeout: float = 2.0,
    ) -> None:
        """Unsubscribe in protocol order while durably handing off earlier frames."""

        sock = self.sock
        connection = self.connection
        if sock is None or connection is None:
            return
        deadline = time.monotonic() + max(0.1, timeout)
        connection.send_command("UNSUBSCRIBE", "usage")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CPAError("CPA usage stream unsubscribe timed out")
            sock.settimeout(remaining)
            try:
                frame = connection.read_response()
            except (socket.timeout, BlockingIOError, ssl.SSLWantReadError):
                continue
            if (
                isinstance(frame, list)
                and len(frame) == 3
                and str(frame[0]).lower() == "unsubscribe"
                and str(frame[1]).lower() == "usage"
                and frame[2] == 0
            ):
                return
            record = decode_usage_message(frame)
            if record is not None:
                on_record(record)

    def ping(
        self,
        on_record: Callable[[dict[str, Any]], None],
        *,
        timeout: float = 2.0,
    ) -> None:
        """Confirm a subscribed connection while handing off earlier messages."""

        sock = self.sock
        connection = self.connection
        if sock is None or connection is None:
            raise CPAError("CPA usage stream is not connected")
        payload = "sub2pool"
        deadline = time.monotonic() + max(0.1, timeout)
        try:
            connection.send_command("PING", payload)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CPAError("CPA usage stream ping timed out")
                sock.settimeout(remaining)
                frame = connection.read_response()
                if (
                    isinstance(frame, list)
                    and len(frame) == 2
                    and str(frame[0]).lower() == "pong"
                    and frame[1] == payload
                ):
                    return
                record = decode_usage_message(frame)
                if record is not None:
                    on_record(record)
        except (socket.timeout, BlockingIOError, ssl.SSLWantReadError) as exc:
            raise CPAError("CPA usage stream ping timed out") from exc
        except CPAError:
            raise
        except (ConnectionError, OSError, ValueError) as exc:
            raise CPAError(
                f"CPA usage stream ping failed：{exc.__class__.__name__}"
            ) from exc

    def read_record(self, timeout: float = 1.0) -> dict[str, Any] | None:
        sock = self.sock
        connection = self.connection
        if sock is None or connection is None:
            raise CPAError("CPA usage stream is not connected")
        sock.settimeout(max(0.0, timeout))
        try:
            return decode_usage_message(connection.read_response())
        except (socket.timeout, BlockingIOError, ssl.SSLWantReadError):
            return None
        except CPAError:
            raise
        except (ConnectionError, OSError, ValueError) as exc:
            raise CPAError(
                f"CPA usage stream disconnected：{exc.__class__.__name__}"
            ) from exc

    def close(self) -> None:
        sock = self.sock
        self.sock = None
        self.connection = None
        if sock is not None:
            sock.close()
