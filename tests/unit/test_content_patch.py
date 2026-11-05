"""OCR 正文补录链路的回归测试。

锁住「原件没有印刷文字 → 永远查不到」这一真实故障的补救闭环：
1. 补录内容有长度上限，避免把巨型文本写进数据库；
2. Paperless PATCH 的 4xx / 连接异常必须转结构化错误，
   否则界面会提示成功但库里没变；
3. 响应契约必须带 ``reindexed``，前端据此告知用户索引正在重建。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gateway.clients import UpstreamError, Upstreams  # noqa: E402
from gateway.schemas import ContentPatchRequest, ContentPatchResponse  # noqa: E402


def test_request_length_bound():
    assert ContentPatchRequest(content="").content == ""
    assert ContentPatchRequest(content="  说明文字  ").content == "  说明文字  "
    with pytest.raises(ValidationError):
        ContentPatchRequest(content="x" * 20001)


def test_response_contract_carries_reindexed():
    r = ContentPatchResponse(doc_id=28, content_length=12, request_id="rid-1")
    assert r.reindexed is True
    assert r.model_dump()["doc_id"] == 28


def _upstream(status: int = 200, payload: dict | None = None, exc: Exception | None = None):
    """绕过 __init__（会读配置并建连接池），只注入待测的 pl client。"""
    up = object.__new__(Upstreams)
    client = AsyncMock()
    if exc is not None:
        client.patch.side_effect = exc
    else:
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload or {}
        client.patch.return_value = resp
    up.pl = client
    up._pl_token = "token"
    return up


def test_pl_patch_success():
    up = _upstream(200, {"id": 28, "content": "补录正文"})
    out = asyncio.run(up.pl_patch("/api/documents/28/", {"content": "补录正文"}))
    assert out["content"] == "补录正文"


def test_pl_patch_maps_4xx():
    up = _upstream(403)
    with pytest.raises(UpstreamError) as e:
        asyncio.run(up.pl_patch("/api/documents/28/", {"content": "x"}))
    assert e.value.status == 502


def test_pl_patch_maps_transport_error():
    up = _upstream(exc=httpx.ConnectError("down"))
    with pytest.raises(UpstreamError) as e:
        asyncio.run(up.pl_patch("/api/documents/28/", {"content": "x"}))
    assert e.value.status == 503
