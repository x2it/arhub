"""上游客户端：共享连接池 + 分层超时 + 指数退避重试 + 熔断 + 内部 Token。

修复原型缺陷：
- 每请求新建 AsyncClient（gateway/app.py 8 处）→ lifespan 单例连接池
- pl_get 未捕获连接异常 → 统一转结构化 502/503
- 无重试 → 幂等 GET 指数退避 2 次
- 无熔断 → semantic 连续失败即熔断，hybrid 自动降级（degraded:true）
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from shared.settings import get_settings

log = logging.getLogger("gateway.clients")


class CircuitOpen(Exception):
    pass


class CircuitBreaker:
    def __init__(self, fail_threshold: int, reset_seconds: int):
        self.fail_threshold = fail_threshold
        self.reset_seconds = reset_seconds
        self._fails = 0
        self._opened_at: Optional[float] = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if time.monotonic() - self._opened_at >= self.reset_seconds:
            self._opened_at = None  # 半开，放行试探
            self._fails = 0
            return True
        return False

    def record(self, ok: bool) -> None:
        if ok:
            self._fails = 0
            self._opened_at = None
            return
        self._fails += 1
        if self._fails >= self.fail_threshold:
            self._opened_at = time.monotonic()
            log.warning("熔断开启，%ss 内快速失败", self.reset_seconds)


class Upstreams:
    """lifespan 持有的共享上游客户端集合。"""

    def __init__(self) -> None:
        s = get_settings()
        self.s = s
        self.pl = httpx.AsyncClient(
            base_url=s.paperless_url,
            timeout=httpx.Timeout(s.timeout_upload, connect=s.timeout_connect),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        self.sem = httpx.AsyncClient(
            base_url=s.semantic_url,
            timeout=httpx.Timeout(s.timeout_semantic + 5, connect=s.timeout_connect),
        )
        self.semantic_breaker = CircuitBreaker(s.circuit_fail_threshold, s.circuit_reset_seconds)
        self._pl_token: str = ""
        self._last_token_attempt = 0.0

    def set_pl_token(self, token: str) -> None:
        self._pl_token = token

    def _load_token(self) -> str:
        """Token 兜底链：环境变量 → archive.sys_config（网关引导期写入）。

        容器休眠恢复/重启时，网关可能先于 Paperless 启动导致引导期换取 token 失败；
        此时库里通常已有上次落库的 token，必须能自动恢复，不能让全站检索瘫痪。
        """
        import os

        token = os.getenv("PAPERLESS_TOKEN", "")
        if token:
            return token
        now = time.monotonic()
        if now - self._last_token_attempt < 3:  # 避免每次请求都打 DB
            return ""
        self._last_token_attempt = now
        try:
            from shared.db import get_pg
            with get_pg() as conn:
                row = conn.execute(
                    "SELECT value FROM archive.sys_config WHERE key='paperless_token'"
                ).fetchone()
            if row and row[0]:
                log.info("Paperless Token 从 sys_config 恢复")
                return row[0]
        except Exception as e:  # noqa: BLE001
            log.warning("读取 paperless_token 失败: %s", e)
        return ""

    def pl_headers(self) -> dict[str, str]:
        if not self._pl_token:
            self._pl_token = self._load_token()
        if not self._pl_token:
            raise RuntimeError("PAPERLESS_TOKEN 未初始化")
        return {"Authorization": f"Token {self._pl_token}"}

    def internal_headers(self) -> dict[str, str]:
        return {"X-Internal-Token": get_settings().internal_token}

    async def aclose(self) -> None:
        await self.pl.aclose()
        await self.sem.aclose()

    # ---------- Paperless ----------
    async def pl_get(self, path: str, params: dict | None = None, retries: int | None = None) -> dict:
        attempts = (self.s.retry_attempts if retries is None else retries) + 1
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                r = await self.pl.get(path, headers=self.pl_headers(), params=params)
                if r.status_code >= 400:
                    # 4xx 不重试（除 429）
                    if r.status_code < 500 and r.status_code != 429:
                        raise httpx.HTTPStatusError(f"paperless {r.status_code}", request=r.request, response=r)
                    raise httpx.HTTPStatusError(f"paperless {r.status_code}", request=r.request, response=r)
                return r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response is not None and e.response.status_code < 500 and e.response.status_code != 429:
                    raise UpstreamError(502, "paperless_error", f"档案服务返回错误({e.response.status_code})") from e
                last_exc = e
                if i < attempts - 1:
                    await asyncio.sleep(0.5 * (2 ** i))
        raise UpstreamError(503, "paperless_unavailable", "档案服务暂不可用，请稍后重试") from last_exc

    async def pl_post_form(self, path: str, data: dict, files: dict) -> httpx.Response:
        try:
            return await self.pl.post(path, headers=self.pl_headers(), data=data, files=files)
        except httpx.TransportError as e:
            raise UpstreamError(503, "paperless_unavailable", "档案服务暂不可用，上传未入队") from e

    async def pl_patch(self, path: str, payload: dict) -> dict:
        """PATCH Paperless 文档字段（补录/修订 OCR 正文）。

        与 pl_get 一致：4xx 立即转结构化错误、连接异常转 503，避免写操作
        在失败时留下「界面提示成功但库里没变」的假象。
        """
        try:
            r = await self.pl.patch(path, headers=self.pl_headers(), json=payload)
        except httpx.TransportError as e:
            raise UpstreamError(503, "paperless_unavailable", "档案服务暂不可用，正文未保存") from e
        if r.status_code >= 400:
            raise UpstreamError(502, "paperless_error",
                                f"档案服务返回错误({r.status_code})") from None
        return r.json()

    # ---------- Semantic（带熔断）----------
    async def sem_request(self, method: str, path: str, **kw) -> dict:
        if not self.semantic_breaker.allow():
            raise CircuitOpen()
        try:
            r = await self.sem.request(method, path, headers=self.internal_headers(), **kw)
            r.raise_for_status()
            self.semantic_breaker.record(True)
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            self.semantic_breaker.record(False)
            raise UpstreamError(503, "semantic_unavailable", "语义服务暂不可用") from e


class UpstreamError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)
