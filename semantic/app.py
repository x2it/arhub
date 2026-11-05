"""语义检索服务 v2.0：常驻内存矩阵 + 内部 Token + 零阻塞索引。

相对 v1 原型的系统性重做：
- 每请求全表 SELECT+vstack+全排序 → 启动常驻 + COW 原子替换 + argpartition
- async def rebuild 里跑同步 encode 阻塞事件循环 → 索引全部剥离到 indexer
  worker；本服务只读矩阵，/index/* 仅入队转发网关 outbox
- 零认证 → X-Internal-Token 保护全部端点
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from semantic.indexer import get_model
from semantic.matrix import get_matrix, reload_matrix
from semantic.store import get_store
from shared.logging_conf import request_id_var, setup_logging
from shared.settings import get_settings

log = logging.getLogger("semantic")

s = get_settings()

# 常驻矩阵自动刷新周期（秒）。worker 写完向量会主动通知刷新，此定时器
# 兜底通知丢失的情况，保证「新档案入库后短时间内即可被语义检索到」。
MATRIX_REFRESH_SECONDS = int(os.getenv("MATRIX_REFRESH_SECONDS", "30"))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    store = get_store()
    store.init_schema()
    get_model()  # 启动预加载（双检锁），首个请求零等待
    # 常驻矩阵载入
    loaded = store.load_all()
    if loaded:
        metas, mat = loaded
        get_matrix().replace(metas, mat)
        log.info("常驻矩阵就绪: %s 向量", len(metas))

    async def _watch_matrix():
        """定时检查向量库是否变化，变化则重载常驻矩阵（兜底）。"""
        while True:
            await asyncio.sleep(MATRIX_REFRESH_SECONDS)
            try:
                stats = await asyncio.to_thread(store.stats)
                cur = (stats.get("chunks"), stats.get("documents"))
                if cur != getattr(app.state, "matrix_sig", None):
                    n = await reload_matrix()
                    app.state.matrix_sig = cur
                    log.info("常驻矩阵自动刷新: %s 向量", n)
            except Exception as e:  # noqa: BLE001
                log.warning("常驻矩阵自动刷新失败: %s", e)

    task = asyncio.create_task(_watch_matrix())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="语义检索服务", version="2.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


def require_internal(request: Request) -> None:
    token = request.headers.get("X-Internal-Token", "")
    if not token or token != os.getenv("INTERNAL_TOKEN", ""):
        raise HTTPException(status_code=403, detail="内部接口拒绝访问")


class SearchHit(BaseModel):
    doc_id: int
    doc_title: str | None = None
    asn: int | None = None
    created: str | None = None
    chunk_index: int | None = None
    text: str
    score: float


@app.get("/health")
def health():
    m = get_matrix()
    return {"status": "ok", "model": s.embed_model, "vectors": m.size, "ready": m.ready}


@app.get("/ready")
def ready():
    m = get_matrix()
    if not m.ready:
        return JSON_503({"status": "degraded", "reason": "matrix not loaded"})
    return {"status": "ok", "vectors": m.size}


def JSON_503(payload: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content=payload)


@app.get("/stats")
def stats(request: Request):
    require_internal(request)
    return get_store().stats()


@app.post("/internal/reload-matrix")
async def reload_matrix_endpoint(request: Request):
    """供 indexer worker 通知：重新载入常驻矩阵。

    worker 写向量库与 API 持有常驻矩阵分处两个进程，worker 写完必须由
    API 亲自重载，否则新档案「已入库但语义检索不到」。
    """
    require_internal(request)
    n = await reload_matrix()
    return {"status": "ok", "vectors": n}


@app.get("/semantic/search", response_model=list[SearchHit])
def search(request: Request, q: str, top_k: int = Query(5, ge=1, le=50)):
    require_internal(request)
    if not q.strip():
        return []
    matrix = get_matrix()
    if not matrix.ready:
        return []
    model = get_model()
    qv = model.encode([s.query_prefix + q], normalize_embeddings=True,
                      show_progress_bar=False)[0]
    hits = matrix.search(np.asarray(qv, dtype=np.float32), top_k,
                         min_score=s.semantic_min_score,
                         rel_margin=s.semantic_rel_margin)
    return [SearchHit(doc_id=h["doc_id"], doc_title=h.get("doc_title"),
                      asn=h.get("asn"), created=h.get("created"),
                      chunk_index=h.get("chunk_index"),  # 修复原型恒 None
                      text=h.get("text") or "", score=h.get("score", 0.0))
            for h in hits]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=s.semantic_port)
