"""Minimal RFC 6455 WebSocket client, standard library only.

`src/` stays stdlib-only (CLAUDE.md), so this implements exactly what a
market-data consumer needs and nothing more: the HTTP upgrade handshake
(with `Sec-WebSocket-Accept` verification), masked client frames, text/
binary messages with fragmentation, ping -> pong, and close. An optional
HTTP CONNECT tunnel covers environments that route egress through a
proxy (`HTTPS_PROXY`).

Everything that goes wrong on the wire raises `ConnectionError` (or its
subclass), which is the signal `data.realtime.ResilientEventFeed` turns
into a reconnect with backoff. A frame that violates the protocol is
never guessed around.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Optional

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_MESSAGE_BYTES = 16 * 1024 * 1024  # bounded memory: refuse absurd frames


class WebSocketProtocolError(ConnectionError):
    pass


class WebSocketClosed(ConnectionError):
    def __init__(self, code: Optional[int], reason: str) -> None:
        super().__init__(f"websocket closed by peer (code={code}, reason={reason!r})")
        self.code = code
        self.reason = reason


def expected_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")


def encode_frame(opcode: int, payload: bytes, *, mask_key: bytes, fin: bool = True) -> bytes:
    """Client-to-server frames are always masked (RFC 6455 5.3)."""
    if len(mask_key) != 4:
        raise ValueError("mask_key must be 4 bytes")
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head.append(0x80 | n)
    elif n < 1 << 16:
        head.append(0x80 | 126)
        head += struct.pack("!H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack("!Q", n)
    head += mask_key
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + masked


class _Reader:
    def __init__(self, recv: Callable[[int], bytes]) -> None:
        self._recv = recv
        self._buf = b""

    def exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("connection closed while reading a frame")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def line(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._recv(4096)
            if not chunk:
                raise ConnectionError("connection closed during handshake")
            self._buf += chunk
            if len(self._buf) > 65536:
                raise WebSocketProtocolError("handshake header too large")
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line


@dataclass(frozen=True)
class Message:
    opcode: int  # OP_TEXT | OP_BINARY | OP_PING | OP_PONG
    payload: bytes

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8")


class WebSocketConnection:
    """One established connection. `sock` is anything with `sendall`,
    `recv`, `close` (a real socket, or a fake in tests)."""

    def __init__(self, sock, *, random_bytes: Callable[[int], bytes] = os.urandom, reader: Optional[_Reader] = None) -> None:
        self._sock = sock
        self._random = random_bytes
        self._reader = reader or _Reader(sock.recv)
        self.closed = False

    def _send(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise ConnectionError("websocket already closed")
        try:
            self._sock.sendall(encode_frame(opcode, payload, mask_key=self._random(4)))
        except OSError as exc:
            raise ConnectionError(f"send failed: {exc}") from exc

    def send_text(self, text: str) -> None:
        self._send(OP_TEXT, text.encode("utf-8"))

    def send_ping(self, payload: bytes = b"") -> None:
        self._send(OP_PING, payload)

    def send_pong(self, payload: bytes = b"") -> None:
        self._send(OP_PONG, payload)

    def _frame(self) -> tuple[bool, int, bytes]:
        try:
            b1, b2 = self._reader.exact(2)
        except socket.timeout as exc:
            raise TimeoutError("no frame within the socket timeout") from exc
        except OSError as exc:
            raise ConnectionError(f"recv failed: {exc}") from exc
        fin, opcode = bool(b1 & 0x80), b1 & 0x0F
        if b1 & 0x70:
            raise WebSocketProtocolError("reserved bits set without a negotiated extension")
        if b2 & 0x80:
            raise WebSocketProtocolError("server frames must not be masked")
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._reader.exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._reader.exact(8))[0]
        if n > MAX_MESSAGE_BYTES:
            raise WebSocketProtocolError(f"frame of {n} bytes exceeds MAX_MESSAGE_BYTES")
        return fin, opcode, self._reader.exact(n)

    def recv(self) -> Message:
        """Next data or control message. Pings are returned to the caller
        (which must answer with `send_pong`), so the caller can record
        heartbeat liveness. A close frame raises `WebSocketClosed`."""
        parts: list[bytes] = []
        first_opcode: Optional[int] = None
        while True:
            fin, opcode, payload = self._frame()
            if opcode == OP_CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                reason = payload[2:].decode("utf-8", errors="replace")
                self.closed = True
                raise WebSocketClosed(code, reason)
            if opcode in (OP_PING, OP_PONG):
                if not fin or len(payload) > 125:
                    raise WebSocketProtocolError("invalid control frame")
                return Message(opcode, payload)
            if opcode == OP_CONTINUATION:
                if first_opcode is None:
                    raise WebSocketProtocolError("continuation frame without a start frame")
            elif opcode in (OP_TEXT, OP_BINARY):
                if first_opcode is not None:
                    raise WebSocketProtocolError("new data frame inside a fragmented message")
                first_opcode = opcode
            else:
                raise WebSocketProtocolError(f"unknown opcode {opcode:#x}")
            parts.append(payload)
            if sum(len(p) for p in parts) > MAX_MESSAGE_BYTES:
                raise WebSocketProtocolError("message exceeds MAX_MESSAGE_BYTES")
            if fin:
                return Message(first_opcode, b"".join(parts))

    def close(self, code: int = 1000) -> None:
        if not self.closed:
            try:
                self._send(OP_CLOSE, struct.pack("!H", code))
            except ConnectionError:
                pass
        self.closed = True
        try:
            self._sock.close()
        except OSError:
            pass


def _open_tcp(host: str, port: int, timeout: float) -> socket.socket:
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        return socket.create_connection((host, port), timeout=timeout)
    p = urllib.parse.urlsplit(proxy)
    sock = socket.create_connection((p.hostname, p.port or 80), timeout=timeout)
    request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if p.username:
        cred = base64.b64encode(f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}".encode()).decode()
        request += f"Proxy-Authorization: Basic {cred}\r\n"
    sock.sendall((request + "\r\n").encode("ascii"))
    reader = _Reader(sock.recv)
    status = reader.line().decode("latin-1")
    while reader.line():
        pass
    if " 200" not in status:
        sock.close()
        raise ConnectionError(f"proxy CONNECT to {host}:{port} failed: {status}")
    return sock


def connect(url: str, *, timeout: float = 10.0, random_bytes: Callable[[int], bytes] = os.urandom) -> WebSocketConnection:
    """Opens `ws://` or `wss://` and completes the handshake. Raises
    `ConnectionError` on any failure (DNS, TLS, non-101 status, bad
    accept key)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise ValueError(f"not a websocket url: {url!r}")
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "wss" else 80)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        sock = _open_tcp(host, port, timeout)
        if parts.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout)
    except OSError as exc:
        raise ConnectionError(f"cannot connect to {host}:{port}: {exc}") from exc
    return handshake(sock, host=host, port=port, path=path, random_bytes=random_bytes)


def handshake(sock, *, host: str, port: int, path: str, random_bytes: Callable[[int], bytes] = os.urandom) -> WebSocketConnection:
    key = base64.b64encode(random_bytes(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        sock.sendall(request.encode("ascii"))
        reader = _Reader(sock.recv)
        status = reader.line().decode("latin-1")
        headers: dict[str, str] = {}
        while True:
            line = reader.line()
            if not line:
                break
            name, _, value = line.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()
    except OSError as exc:
        raise ConnectionError(f"handshake failed: {exc}") from exc
    if " 101" not in status:
        sock.close()
        raise ConnectionError(f"websocket upgrade refused: {status}")
    if headers.get("sec-websocket-accept") != expected_accept(key):
        sock.close()
        raise WebSocketProtocolError("Sec-WebSocket-Accept mismatch")
    return WebSocketConnection(sock, random_bytes=random_bytes, reader=reader)
