"""Minimal Chrome DevTools Protocol client.

Just enough CDP to find a page and evaluate JavaScript in it — used to press the
provider's Play button after the browser opens, since the streaming URL contains
a server-minted session id and therefore cannot be linked to directly.

Implemented on stdlib sockets rather than a websocket library: Decky's Python
environment does not guarantee third-party packages, and vendoring one for two
frames of traffic is not worth it.
"""

import base64
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

_OPCODE_TEXT = 0x1
_OPCODE_CLOSE = 0x8


class CDPError(Exception):
    pass


def list_targets(port: int, timeout: float = 2.0) -> List[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise CDPError(f"CDP not reachable on {port}: {exc}") from exc


def wait_for_page(port: int, url_contains: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """Poll until a page target whose URL contains `url_contains` exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for target in list_targets(port):
                if target.get("type") == "page" and url_contains in target.get("url", ""):
                    return target
        except CDPError:
            pass
        time.sleep(0.5)
    return None


class _WebSocket:
    """A client websocket good enough for CDP: text frames, no extensions."""

    def __init__(self, url: str, timeout: float = 15.0):
        if not url.startswith("ws://"):
            raise CDPError(f"only ws:// supported, got {url!r}")
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self._sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        self._sock.settimeout(timeout)
        self._handshake(host, int(port or 80), "/" + path)
        self._buffer = b""

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode())

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CDPError("connection closed during handshake")
            response += chunk
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise CDPError(f"handshake refused: {response.split(chr(13).encode())[0]!r}")

    def send_text(self, payload: str) -> None:
        data = payload.encode()
        header = bytearray([0x80 | _OPCODE_TEXT])
        mask = os.urandom(4)
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(bytes(header) + masked)

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CDPError("connection closed")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length) if length else b""
            if opcode == _OPCODE_CLOSE:
                raise CDPError("server closed the connection")
            if opcode == _OPCODE_TEXT:
                return payload.decode(errors="replace")
            # ignore ping/pong/continuation for our purposes

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def evaluate(ws_url: str, expression: str, timeout: float = 15.0) -> Any:
    """Run JS in a page and return its value."""
    ws = _WebSocket(ws_url, timeout=timeout)
    try:
        ws.send_text(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(ws.recv_text())
            if message.get("id") == 1:
                result = message.get("result", {}).get("result", {})
                return result.get("value")
        raise CDPError("timed out waiting for evaluate result")
    finally:
        ws.close()
