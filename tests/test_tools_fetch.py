"""fetch_url tool tests — against a local HTTP server, no external network."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentic_os.memory import Memory
from agentic_os.tools import ToolBox

PAGE = b"""<html><head><title>t</title><style>p{color:red}</style>
<script>alert('nope')</script></head>
<body><h1>Hello Page</h1><p>useful   text</p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)


@pytest.fixture
def toolbox(tmp_path):
    config = {"workspace": tmp_path, "shell_timeout": 10}
    return ToolBox(config, Memory(tmp_path / "m.db"), approve=lambda _: True)


def test_fetch_url_returns_visible_text_only(toolbox):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        text = toolbox.execute("fetch_url", {"url": url})
        assert "Hello Page" in text
        assert "useful   text" in text
        assert "alert" not in text  # scripts stripped
        assert "color:red" not in text  # styles stripped
    finally:
        httpd.shutdown()


def test_fetch_url_rejects_non_http_schemes(toolbox):
    with pytest.raises(ValueError, match="only http"):
        toolbox.execute("fetch_url", {"url": "file:///etc/passwd"})
