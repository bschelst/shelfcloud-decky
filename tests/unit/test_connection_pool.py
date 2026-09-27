# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The shared connection pool and the exception contract it keeps.

Pooling turns 40 connections into 1. The tricky half is errors: callers were
written against urlopen and depend on HTTPError (304, 401) and URLError, so
_open has to translate urllib3's exceptions back. These tests cover that.
"""

import http.server
import sys
import threading
import time
import urllib.error
from unittest import mock

import pytest

sys.path.insert(0, "tests/unit")
import test_input_validation as harness            # noqa: E402  (decky stubs)

main = harness.main


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"                  # keep-alive, or nothing pools
    status = 200

    def do_GET(self):
        code = type(self).status
        body = b'{"ok":true}' if code == 200 else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format, *args):      # noqa: A002
        pass


class _CountingServer(http.server.ThreadingHTTPServer):
    """A server that counts how many TCP connections it accepted."""
    accepted = 0

    def get_request(self):
        conn = super().get_request()
        type(self).accepted += 1
        return conn


@pytest.fixture
def server():
    _CountingServer.accepted = 0
    _Handler.status = 200
    srv = _CountingServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # A fresh pool per test, or reuse from an earlier one skews the count.
    main._POOL = None
    yield srv, "http://127.0.0.1:%d/x" % srv.server_address[1]
    srv.shutdown()
    main._POOL = None


# ------------------------------------------------------------- the pooling

def test_many_requests_share_one_connection(server):
    _, url = server
    for _ in range(40):
        with main._open(url) as response:
            response.read()

    # the whole point: before the pool this was 40
    assert _CountingServer.accepted == 1, (
        "expected the connection to be reused, got %d handshakes"
        % _CountingServer.accepted)


def test_a_good_reply_still_reads_normally(server):
    _, url = server
    with main._open(url) as response:
        assert response.read() == b'{"ok":true}'
        assert response.headers.get("Content-Type") == "application/json"


# --------------------------------------------- the exception contract

@pytest.mark.parametrize("code", [304, 401, 404, 500])
def test_a_non_2xx_raises_HTTPError_with_its_code(server, code):
    """_request reads .code to tell 304 from 401. urllib3 would not raise."""
    _, url = server
    _Handler.status = code
    with pytest.raises(urllib.error.HTTPError) as caught:
        main._open(url).read()
    assert caught.value.code == code


def test_an_unreachable_host_raises_URLError_not_urllib3s_error(server):  # noqa: ARG001
    """_request's SSL diagnostic catches URLError only. urllib3 raises
    MaxRetryError here, which would sail past it untranslated."""
    with pytest.raises(urllib.error.URLError):
        main._open("http://127.0.0.1:1/nope", timeout=2).read()


def test_an_unreachable_host_fails_quickly_rather_than_hanging(server):  # noqa: ARG001
    started = time.time()
    with pytest.raises(urllib.error.URLError):
        main._open("http://127.0.0.1:1/nope", timeout=2).read()
    assert time.time() - started < 5


def test_failures_do_not_leak_the_pool(server):
    """A run of errors must not exhaust the pool and wedge later calls."""
    _, url = server
    _Handler.status = 500
    for _ in range(60):
        with pytest.raises(urllib.error.HTTPError):
            main._open(url).read()

    _Handler.status = 200
    with main._open(url) as response:
        assert response.read() == b'{"ok":true}'


def test_it_still_works_where_urllib3_is_missing(server):
    """Without urllib3 fall back to a connection per call, not a crash."""
    _, url = server
    with mock.patch.object(main, "_urllib3", None):
        opened = main._open(url)
        try:
            assert opened.read()
        finally:
            opened.close()
