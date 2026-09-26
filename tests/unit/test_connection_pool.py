# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The shared connection pool, and the exception contract it has to keep.

urlopen opened a NEW TCP connection -- and a TLS handshake with it -- for
every call. A sync makes hundreds: a layout per new tile, six artwork slots
per changed game, a slice per two megabytes of a hero, a player count per page
opened. Measured here: 40 requests were 40 connections, and are now 1.

The delicate half is not the pooling, it is the ERRORS. Every caller in
main.py was written against urlopen, and two of them depend on the exact
exception:

  * _request treats HTTPError 304 as "what you already hold is current" and
    HTTPError 401 as "sign out" -- urllib3 does not raise for a status at all,
    it puts it in the response.
  * _request's URLError handler is the one place that names a certificate
    failure in the log -- and urllib3 raises MaxRetryError/SSLError there,
    which sailed straight past it until this file caught it.

So _open translates, and these tests are about the translation.

Run: python3 -m pytest tests/unit/test_connection_pool.py
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

    # The whole point. Before the pool this was 40.
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
    """The SSL diagnostic in _request catches URLError and nothing else.

    urllib3 raises MaxRetryError here. Untranslated it sails past that
    handler, and the one log line that says "your certificate is wrong"
    stops being printed.
    """
    with pytest.raises(urllib.error.URLError):
        main._open("http://127.0.0.1:1/nope", timeout=2).read()


def test_an_unreachable_host_fails_quickly_rather_than_hanging(server):  # noqa: ARG001
    started = time.time()
    with pytest.raises(urllib.error.URLError):
        main._open("http://127.0.0.1:1/nope", timeout=2).read()
    assert time.time() - started < 5


def test_failures_do_not_leak_the_pool(server):
    """A run of errors must not exhaust it -- that would wedge every later
    call as surely as a dead socket."""
    _, url = server
    _Handler.status = 500
    for _ in range(60):
        with pytest.raises(urllib.error.HTTPError):
            main._open(url).read()

    _Handler.status = 200
    with main._open(url) as response:
        assert response.read() == b'{"ok":true}'


def test_it_still_works_where_urllib3_is_missing(server):
    """Decky ships urllib3, but a plugin that dies without it is worse than
    one that opens a connection per call."""
    _, url = server
    with mock.patch.object(main, "_urllib3", None):
        opened = main._open(url)
        try:
            assert opened.read()
        finally:
            opened.close()
