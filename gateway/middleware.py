"""中间件：请求ID 链路 + 令牌桶限流。"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from shared.logging_conf import actor_var, request_id_var
from shared.settings import get_settings

import uuid


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request_id_var.set(rid)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - start) * 1000))
        return response


class _Bucket:
    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events: deque[float] = deque()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 (identity, route_template) 的简单滑动窗口限流。"""

    def __init__(self, app, per_min: int):
        super().__init__(app)
        self.per_min = per_min
        self._buckets: dict[tuple, _Bucket] = {}
        self._last_gc = time.monotonic()

    def _identity(self, request: Request) -> str:
        auth = request.headers.get("Authorization", "")
        if auth:
            return f"tok:{auth[-24:]}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def dispatch(self, request: Request, call_next):
        # /health /ready /metrics 不限流（监控探测）
        if request.url.path in ("/health", "/ready", "/metrics"):
            return await call_next(request)
        key = (self._identity(request), request.url.path.rstrip("/").rsplit("/", 1)[0] or "/")
        now = time.monotonic()
        bucket = self._buckets.setdefault(key, _Bucket())
        q = bucket.events
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_min:
            from gateway.errors import error_response

            retry_after = max(1, int(60 - (now - q[0])))
            resp = error_response(429, "rate_limited", "请求过于频繁，请稍后重试")
            resp.headers["Retry-After"] = str(retry_after)
            resp.headers["X-RateLimit-Limit"] = str(self.per_min)
            return resp
        q.append(now)
        # 简单 GC 防 map 膨胀
        if now - self._last_gc > 300:
            self._last_gc = now
            if len(self._buckets) > 10000:
                self._buckets.clear()
        return await call_next(request)


def bind_actor(username: str) -> None:
    actor_var.set(username)
