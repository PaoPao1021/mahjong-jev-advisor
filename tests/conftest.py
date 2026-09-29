import pytest
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@pytest.fixture(autouse=True)
def isolate_user_configuration(tmp_path, monkeypatch):
    """UI tests must never change the real user's credentials or calibration."""
    monkeypatch.setenv("MAHJONG_ADVISOR_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("mahjong_jev_advisor.settings.config_dir", lambda: tmp_path)
    monkeypatch.setattr("mahjong_jev_advisor.vision.config_dir", lambda: tmp_path)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.fixture
def http_server():
    """An actual loopback HTTP server, not a patched network client."""
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.received.append((self.path, dict(self.headers), body))
            self.send_response(self.server.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.server.reply).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.received = []
    server.status = 200
    server.reply = {"model": "test-model", "answers": {"action": {"type": "choice", "choice": "a0"}}}
    server.endpoint = f"http://127.0.0.1:{server.server_port}/inference"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(2)
