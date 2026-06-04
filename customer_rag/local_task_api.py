from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from customer_rag.config import load_config
from customer_rag.raw_jobs import recover_interrupted_raw_job, start_raw_job
from customer_rag.subscription_jobs import recover_interrupted_subscription_job, read_job_state, request_stop_subscription_job


_server: ThreadingHTTPServer | None = None
_lock = threading.Lock()


def ensure_local_task_api(port: int = 8512) -> str:
    global _server
    with _lock:
        if _server is not None:
            return f"http://127.0.0.1:{port}"
        _server = ThreadingHTTPServer(("127.0.0.1", port), _TaskApiHandler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}"


class _TaskApiHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature.
        return

    def do_OPTIONS(self) -> None:
        self._send_json({})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        config = load_config()
        if parsed.path == "/raw/start":
            task = query.get("task", [""])[0]
            self._send_json(asdict(start_raw_job(config, task)))
            return
        if parsed.path == "/raw/status":
            self._send_json(asdict(recover_interrupted_raw_job(config)))
            return
        if parsed.path == "/subscription/status":
            self._send_json(asdict(recover_interrupted_subscription_job(config)))
            return
        if parsed.path == "/subscription/stop":
            request_stop_subscription_job(config)
            self._send_json(asdict(read_job_state(config)))
            return
        self._send_json({"error": "not found"}, status=404)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
