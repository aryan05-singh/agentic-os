"""Browser layer tests — drive real headless Chromium against a local
HTTP server. Skipped entirely if playwright (or its chromium) isn't installed.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from agentic_os.browser import BrowserSession  # noqa: E402

INDEX = b"""<!doctype html><title>Test Home</title><body>
<h1>Welcome home</h1>
<a href="/page2">Go to page two</a>
<input type="text" name="q" placeholder="search box">
<button onclick="document.querySelector('h1').textContent='Clicked!'">Press me</button>
</body>"""

PAGE2 = b"""<!doctype html><title>Page Two</title><body>
<h1>You made it to page two</h1>
</body>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE2 if self.path == "/page2" else INDEX
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    s = BrowserSession(tmp_path_factory.mktemp("ws"))
    yield s
    s.close()


def test_goto_snapshot_lists_elements(session, server_url):
    snap = session.goto(server_url)
    assert "Test Home" in snap
    assert "Welcome home" in snap
    assert "Go to page two" in snap
    assert "input:text" in snap  # the search box shows up typed and numbered
    assert "[0]" in snap


def test_click_by_number_navigates(session, server_url):
    session.goto(server_url)
    snap = session.click("0")  # [0] is the link
    assert "page two" in snap.lower()


def test_click_by_selector_mutates_dom(session, server_url):
    session.goto(server_url)
    snap = session.click("text=Press me")
    assert "Clicked!" in snap


def test_type_fills_input(session, server_url):
    session.goto(server_url)
    session.type_text("input[name=q]", "hello world")
    assert session._page.input_value("input[name=q]") == "hello world"


def test_stale_element_number_rejected(session, server_url):
    session.goto(server_url)
    with pytest.raises(ValueError, match="not in the last snapshot"):
        session.click("99")


def test_non_http_url_rejected(session):
    with pytest.raises(ValueError, match="only http"):
        session.goto("file:///etc/passwd")


def test_screenshot_confined_to_workspace(session, server_url):
    session.goto(server_url)
    result = session.screenshot("shots/home.png")
    path = Path(result.split(" to ")[-1])
    assert path.exists() and path.stat().st_size > 0
    with pytest.raises(ValueError, match="escapes workspace"):
        session.screenshot("../outside.png")
