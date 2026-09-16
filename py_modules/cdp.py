# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
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


def hold_platform(ws_url: str, user_agent: str, platform: str = "Windows",
                  platform_version: str = "10.0.0", timeout: float = 15.0) -> "_WebSocket":
    """Tell one page it is running on another OS, and keep telling it.

    Returns the OPEN socket. The caller must keep that object alive for as long
    as the lie has to hold — a CDP emulation override belongs to the session
    that set it, so closing the socket reverts it instantly. That is exactly
    why evaluate() cannot be used for this: it opens and closes a socket per
    call, so an override set through it is gone before the page reads it.

    Why this is needed at all, when the launcher already passes --user-agent:
    that flag changes the User-Agent STRING and nothing else. Measured on the
    device with the flag on, navigator.platform was still "Linux x86_64" and
    navigator.userAgentData.platform was still "Linux", and there is no
    command-line flag for either (--disable-features=UserAgentClientHint was
    tried and changed nothing). Emulation.setUserAgentOverride is the only
    thing that moves the string, navigator.platform, navigator.userAgentData
    and the Sec-CH-UA-Platform request headers together.

    No CDP domain is enabled here on purpose. Domains push events, nothing
    reads this socket once it is set up, and an unread socket whose peer keeps
    writing eventually blocks that peer. With no domain enabled the only
    traffic is the reply below, which is read before returning.
    """
    ws = _WebSocket(ws_url, timeout=timeout)
    try:
        ws.send_text(json.dumps({
            "id": 1,
            "method": "Emulation.setUserAgentOverride",
            "params": {
                "userAgent": user_agent,
                "platform": platform,
                "userAgentMetadata": {
                    "brands": [
                        {"brand": "Microsoft Edge", "version": "153"},
                        {"brand": "Chromium", "version": "153"},
                        {"brand": "Not_A Brand", "version": "8"},
                    ],
                    "fullVersion": "153.0.0.0",
                    "platform": platform,
                    "platformVersion": platform_version,
                    "architecture": "x86",
                    "bitness": "64",
                    "model": "",
                    "mobile": False,
                },
            },
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(ws.recv_text())
            if message.get("id") == 1:
                if "error" in message:
                    raise CDPError(f"override refused: {message['error']}")
                return ws
        raise CDPError("timed out setting the platform override")
    except Exception:
        ws.close()
        raise


def reload_page(ws: "_WebSocket", timeout: float = 15.0) -> None:
    """Reload so the document is built with the override already in force.

    Without this the page has already run its checks against the real platform
    and cached the answer; the override would only affect whatever it asks
    afterwards. Page.enable is deliberately NOT sent — reload does not need it,
    and enabling the domain would start an event stream nobody reads.
    """
    ws.send_text(json.dumps({"id": 2, "method": "Page.reload",
                             "params": {"ignoreCache": False}}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if json.loads(ws.recv_text()).get("id") == 2:
            return
    raise CDPError("timed out reloading the page")
