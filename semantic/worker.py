"""indexer worker：outbox 消费 + 轮询 diff 兜底 + 全量对账。

事件驱动主来源：Paperless post-consume → gateway /internal/index → index_tasks
兜底：轮询 /api/documents/?ordering=-modified diff；-1 任务 = 全量对账
（含删除同步：Paperless 已删 → 向量库清理）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import numpy as np

from semantic.indexer import StoreOps, fetch_all_documents, fetch_document, index_document_sync
from semantic.matrix import get_matrix
from semantic.store import get_store
from shared.db import get_pg
from shared.logging_conf import setup_logging

log = logging.getLogger("semantic.worker")

POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "20"))
BACKOFF_BASE = 5
MAX_ATTEMPTS = 6


def claim_tasks(limit: int = 10) -> list[dict]:
    """原子领取任务：pending → running。"""
    with get_pg() as conn:
        rows = conn.execute(
            "UPDATE archive.index_tasks SET status='running'"
            " WHERE id IN (SELECT id FROM archive.index_tasks"
            "  WHERE status='pending' AND run_after<=now()"
            "  ORDER BY id LIMIT %s) RETURNING id,doc_id,op,attempts",
            (limit,)).fetchall()
    return [{"id": r[0], "doc_id": r[1], "op": r[2], "attempts": r[3]} for r in rows]


def finish_task(task_id: int, ok: bool, error: str = "") -> None:
    with get_pg() as conn:
        if ok:
            conn.execute(
                "UPDATE archive.index_tasks SET status='done', finished_at=now(),"
                " last_error='' WHERE id=%s", (task_id,))
        else:
            conn.execute(
                "UPDATE archive.index_tasks SET attempts=attempts+1, last_error=%s,"
                " status=CASE WHEN attempts+1 >= %s THEN 'dead' ELSE 'pending' END,"
                " run_after=now() + make_interval(secs => %s * (attempts+1))"
                " WHERE id=%s", (error[:500], MAX_ATTEMPTS, BACKOFF_BASE, task_id))


async def process_task(task: dict) -> bool:
    ops = StoreOps()
    doc_id, op = task["doc_id"], task["op"]
    try:
        if op == "delete":
            ops.delete(doc_id)
            log.info("已删除索引 doc_id=%s", doc_id)
            return True
        if doc_id == -1:  # 全量对账
            return await reconcile()
        import httpx

        from shared.settings import get_settings

        async with httpx.AsyncClient(base_url=get_settings().paperless_url,
                                     timeout=60) as client:
            doc = await fetch_document(doc_id, client)
        if doc is None:
            ops.delete(doc_id)  # 文档已不存在 → 清理（删除同步）
            return True
        return await asyncio.to_thread(index_document_sync, ops, doc) is not None
    except Exception as e:
        log.exception("任务 %s 失败: %s", task["id"], e)
        return False


async def reconcile() -> bool:
    """对账：全量 upsert + 删除同步。"""
    t0 = time.monotonic()
    ops = StoreOps()
    docs = await fetch_all_documents()
    active = {d["id"] for d in docs}
    for doc in docs:
        await asyncio.to_thread(index_document_sync, ops, doc)
    stale = ops.store.active_doc_ids() - active
    for doc_id in stale:
        ops.delete(doc_id)
        log.info("对账删除陈旧索引 doc_id=%s", doc_id)
    await reload_matrix()
    log.info("对账完成：docs=%s stale=%s 耗时=%.1fs",
             len(docs), len(stale), time.monotonic() - t0)
    return True


async def reload_matrix() -> None:
    store = get_store()
    loaded = await asyncio.to_thread(store.load_all)
    matrix = get_matrix()
    if loaded:
        metas, mat = loaded
        matrix.replace(metas, mat)
        log.info("矩阵已刷新: %s 向量", len(metas))
    else:
        matrix.replace([], np.zeros((0, 512), dtype=np.float32))


async def main() -> None:
    setup_logging()
    log.info("indexer worker 启动 poll=%ss", POLL_SECONDS)
    store = get_store()
    store.init_schema()
    # 启动即对账一次（替代原型"手动全量重建"）
    try:
        await reconcile()
    except Exception as e:
        log.error("启动对账失败（轮询重试）: %s", e)
        with get_pg() as conn:
            conn.execute("INSERT INTO archive.index_tasks(doc_id,op) VALUES (-1,'upsert')")
    while True:
        try:
            tasks = claim_tasks()
            for t in tasks:
                ok = await process_task(t)
                finish_task(t["id"], ok, error="process failed" if not ok else "")
        except Exception:
            log.exception("worker 循环异常")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
