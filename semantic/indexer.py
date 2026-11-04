"""索引执行器：切片 → encode → 写库 → 刷新常驻矩阵。

从原型 semantic/app.py 的 index_documents/delete_doc/fetch_all_documents
演进而来；CPU 密集 encode 只在 indexer worker / to_thread 中执行。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading
from typing import Optional

import httpx
import numpy as np

from semantic.store import VecDoc, get_store
from shared.settings import get_settings

log = logging.getLogger("semantic.indexer")
_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")

_model = None
_model_lock = threading.Lock()


def get_model():
    """双检锁单例（修复原型无锁懒加载）。"""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                s = get_settings()
                log.info("加载模型 %s ...", s.embed_model)
                _model = SentenceTransformer(s.embed_model)
                log.info("模型加载完成")
    return _model


def split_text(text: str, size: Optional[int] = None, overlap: Optional[int] = None) -> list[str]:
    s = get_settings()
    size = size or s.chunk_size
    overlap = overlap or s.chunk_overlap
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    sentences = [t.strip() for t in _SENT_SPLIT.split(text) if t.strip()]
    chunks, cur = [], ""
    for sent in sentences:
        if len(cur) + len(sent) <= size:
            cur += sent
        else:
            if cur:
                chunks.append(cur)
            cur = (cur[-overlap:] if overlap and len(cur) > overlap else "") + sent
    if cur:
        chunks.append(cur)
    return chunks


_pl_token: Optional[str] = None


def paperless_headers() -> dict:
    """取 Paperless API Token：环境变量 → 内存缓存 → archive.sys_config（网关写入）。"""
    global _pl_token
    if _pl_token:
        return {"Authorization": f"Token {_pl_token}"}
    token = os.getenv("PAPERLESS_TOKEN", "")
    if not token:
        try:
            from shared.db import get_pg
            with get_pg() as conn:
                row = conn.execute(
                    "SELECT value FROM archive.sys_config WHERE key='paperless_token'"
                ).fetchone()
            token = row[0] if row else ""
        except Exception as e:  # noqa: BLE001
            log.warning("读取 paperless_token 失败: %s", e)
    if not token:
        raise RuntimeError("PAPERLESS_TOKEN 未配置（网关尚未完成引导）")
    _pl_token = token
    return {"Authorization": f"Token {token}"}


async def fetch_document(doc_id: int, client: httpx.AsyncClient) -> Optional[dict]:
    try:
        r = await client.get(f"/api/documents/{doc_id}/", headers=paperless_headers())
        if r.status_code == 200:
            return r.json()
        log.error("取文档 %s 失败 HTTP %s", doc_id, r.status_code)
    except httpx.HTTPError as e:
        log.error("取文档 %s 网络错误: %s", doc_id, e)
    return None


async def fetch_all_documents() -> list[dict]:
    """分页收集全量 ID，再并发拉详情（并发闸门 8）。"""
    from shared.settings import get_settings

    async with httpx.AsyncClient(
            base_url=get_settings().paperless_url, timeout=60) as client:
        page, ids = 1, []
        while True:
            r = await client.get("/api/documents/", headers=paperless_headers(),
                                 params={"page": page, "page_size": 100})
            r.raise_for_status()
            data = r.json()
            ids.extend(d["id"] for d in data.get("results", []))
            if not data.get("next") or page > 1000:  # 页码护栏防死循环
                break
            page += 1
        sem = asyncio.Semaphore(8)

        async def one(i: int):
            async with sem:
                return await fetch_document(i, client)

        results = await asyncio.gather(*[one(i) for i in ids])
    return [r for r in results if r]


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def ensure_doc_security(doc_id: int, default_level: int = 1) -> None:
    """为尚无密级记录的档案补默认密级（1=内部）。

    网关的越权过滤是 fail-closed（无记录即拒绝），因此任何入库路径都必须保证
    有密级记录，否则文档会「索引成功但检索不到」。默认取 1 而非 0：公开是最
    宽松级别，不应作为缺省值静默授予。
    """
    try:
        from shared.db import get_pg
        with get_pg() as conn:
            conn.execute(
                "INSERT INTO archive.doc_security(doc_id,classification,tenant_id)"
                " VALUES (%s,%s,%s) ON CONFLICT (doc_id) DO NOTHING",
                (doc_id, default_level, get_settings().tenant_id))
    except Exception as e:  # noqa: BLE001
        log.warning("补默认密级失败 doc_id=%s: %s", doc_id, e)


def index_document_sync(db_ops, doc: dict) -> dict:
    """同步执行（调用方负责放到线程）：编码 + 写库。返回统计。"""
    model = get_model()
    s = get_settings()
    doc_id = doc["id"]
    ensure_doc_security(doc_id)
    content = (doc.get("content") or "").strip()
    if not content:
        return {"doc_id": doc_id, "status": "empty", "chunks": 0}
    sha = content_hash(content)
    prev = db_ops.get_doc_sha(doc_id)
    if prev == sha:
        return {"doc_id": doc_id, "status": "unchanged", "chunks": 0}  # 幂等跳过
    chunks = split_text(content)
    if not chunks:
        return {"doc_id": doc_id, "status": "empty", "chunks": 0}
    vectors = model.encode(chunks, batch_size=s.embed_batch_size,
                           normalize_embeddings=True, show_progress_bar=False)
    idxs = list(range(len(chunks)))
    db_ops.replace_doc(doc_id, doc, sha, chunks,
                       np.asarray(vectors, dtype=np.float32), idxs)
    return {"doc_id": doc_id, "status": "indexed", "chunks": len(chunks)}


class StoreOps:
    """对 store 的封装：幂等读 + replace + delete。"""

    def __init__(self) -> None:
        self.store = get_store()

    def get_doc_sha(self, doc_id: int) -> Optional[str]:
        return self.store.get_doc_sha(doc_id)

    def replace_doc(self, doc_id, doc, sha, chunks, vectors, idxs) -> None:
        self.store.upsert_doc(VecDoc(doc_id=doc_id, title=doc.get("title"),
                                     created=doc.get("created"),
                                     asn=doc.get("archive_serial_number"),
                                     content_sha=sha))
        self.store.replace_chunks(doc_id, doc.get("title") or "", chunks, vectors, idxs)
        self.store.mark_synced(doc_id, sha)
        refresh_matrix_bg()

    def delete(self, doc_id: int) -> None:
        self.store.delete_doc(doc_id)
        refresh_matrix_bg()


def refresh_matrix_bg() -> None:
    """索引变更后通知 **semantic API 进程** 刷新常驻矩阵。

    worker（indexer 容器）与 API（semantic 容器）是两个独立进程，worker
    进程内的矩阵刷新对 API 毫无意义——必须由 API 自己重新载入向量库。
    这里通过内部接口 POST /internal/reload-matrix 触发；失败不影响写库，
    由周期对账兜底。
    """
    from shared.settings import get_settings

    async def _run():
        try:
            url = get_settings().semantic_url.rstrip("/") + "/internal/reload-matrix"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, headers={"X-Internal-Token": os.getenv("INTERNAL_TOKEN", "")})
            if r.status_code == 200:
                log.info("已通知 API 刷新常驻矩阵")
            else:
                log.warning("通知 API 刷新矩阵返回 HTTP %s", r.status_code)
        except Exception as e:
            log.error("矩阵刷新失败（对账兜底）: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass
