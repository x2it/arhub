#!/usr/bin/env python3
"""档枢 ArcHub 发布适配器：单端口反向代理。

发布平台只接受「监听 $PORT 的单个 HTTP 服务」，而本系统是 6 容器生产栈
（Paperless + PostgreSQL + Redis + 语义服务 + 索引器 + 网关）。本代理把
发布流量转发到本机已运行的网关 8700，因此对外提供的是**完整真实系统**，
不是静态占位页。零第三方依赖（仅标准库），便于发布环境直接拉起。

用法：PORT=3000 python3 deploy/publish_proxy.py
"""
from __future__ import annotations

import http.client
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_HOST = os.getenv("ARC_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.getenv("ARC_UPSTREAM_PORT", "8700"))
PORT = int(os.getenv("PORT", "3000"))
TIMEOUT = 120

# 逐跳（hop-by-hop）头不能转发
HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默，避免日志刷屏
        pass

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        path = self.path

        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP:
                headers[k] = v
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        # 传递真实来源，便于网关审计日志记录公网 IP
        fwd = self.headers.get("X-Forwarded-For")
        headers["X-Forwarded-For"] = (
            f"{fwd}, {self.client_address[0]}" if fwd else self.client_address[0]
        )
        headers["X-Forwarded-Proto"] = "https"

        try:
            conn = http.client.HTTPConnection(
                UPSTREAM_HOST, UPSTREAM_PORT, timeout=TIMEOUT)
            conn.request(self.command, path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            status, reason = resp.status, resp.reason
            resp_headers = list(resp.getheaders())
            conn.close()
        except Exception as e:  # noqa: BLE001
            self._error(502, f"upstream unavailable: {type(e).__name__}")
            return

        try:
            self.send_response(status, reason)
            for k, v in resp_headers:
                lk = k.lower()
                if lk in HOP or lk == "content-length":
                    continue
                if lk == "location" and v.startswith("http://127.0.0.1:8700"):
                    v = v[len("http://127.0.0.1:8700"):]
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Powered-By", "ArcHub")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, code: int, msg: str) -> None:
        payload = f'{{"error":"{msg}"}}'.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _proxy


def main() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Proxy)
    srv.daemon_threads = True
    print(f"ArcHub publish proxy :{PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
