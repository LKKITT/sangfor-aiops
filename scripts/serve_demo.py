#!/usr/bin/env python3
"""静态托管前端 dist + 反向代理 /api 到后端。

用于离线沙箱演示：把 built dist 跑在 8610，/api/* 转发到 127.0.0.1:8600。
用法：
    python3 scripts/serve_demo.py
"""
import http.server
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
BACKEND = "http://127.0.0.1:8600"
PORT = 8610
PREFIXES = ("/api/",)

socketserver.TCPServer.allow_reuse_address = True


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(DIST), **kw)

    def log_message(self, fmt, *args):
        # 静音，避免刷屏
        pass

    # ---- /api 反代 ----
    def _proxy(self, method: str):
        url = BACKEND + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "connection"):
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:   # noqa: BLE001
            msg = f'{{"detail":"proxy error: {e}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):
        if self.path.startswith(PREFIXES):
            return self._proxy("GET")
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith(PREFIXES):
            return self._proxy("POST")
        self.send_error(404)

    def do_PUT(self):
        if self.path.startswith(PREFIXES):
            return self._proxy("PUT")
        self.send_error(404)

    def do_PATCH(self):
        if self.path.startswith(PREFIXES):
            return self._proxy("PATCH")
        self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith(PREFIXES):
            return self._proxy("DELETE")
        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()


if __name__ == "__main__":
    if not DIST.exists():
        print(f"dist 不存在：{DIST}\n请先执行 cd frontend && npm run build")
        sys.exit(1)
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"演示服务: http://127.0.0.1:{PORT}  (dist={DIST}, api→{BACKEND})")
        httpd.serve_forever()
