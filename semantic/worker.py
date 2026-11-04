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
RECONCILE_SECONDS = int(os.getenv("WORKER_RECONCILE_SECONDS", "60"))
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


async def reconcile_missing() -> int:
    """补齐「已入库但缺密级记录」的档案：登记默认密级 + 入队增量索引。

    网关的越权过滤是 fail-closed（无 doc_security 记录即不可见），因此任何入库
    路径都必须有密级记录，否则文档会「OCR 完成、索引在库，却检索不到」。
    事件驱动的 post-consume 回调偶发失败时，本兜底让文档在短时间内变为可检索。
    """
    docs = await fetch_all_documents()
    ids = [d["id"] for d in docs]
    if not ids:
        return 0
    with get_pg() as conn:
        rows = conn.execute(
            "SELECT doc_id FROM archive.doc_security WHERE doc_id = ANY(%s)", (ids,)
        ).fetchall()
    have = {r[0] for r in rows}
    missing = [i for i in ids if i not in have]
    if not missing:
        return 0

    from shared.settings import get_settings

    tenant = get_settings().tenant_id
    with get_pg() as conn:
        for doc_id in missing:
            conn.execute(
                "INSERT INTO archive.doc_security(doc_id,classification,tenant_id)"
                " VALUES (%s,1,%s) ON CONFLICT (doc_id) DO NOTHING", (doc_id, tenant))
            conn.execute(
                "INSERT INTO archive.index_tasks(doc_id,op) VALUES (%s,'upsert')", (doc_id,))
    log.info("reconcile_missing 补齐 %s 份档案的密级与索引任务", len(missing))
    return len(missing)


def cleanup_pending_classification() -> None:
    """清理超过 24 小时仍未落定的暂存密级记录（上传失败/文档被删的残留）。"""
    try:
        with get_pg() as conn:
            n = conn.execute(
                "DELETE FROM archive.pending_classification"
                " WHERE created_at < now() - interval '24 hours'").rowcount
        if n:
            log.info("清理过期暂存密级 %s 条", n)
    except Exception as e:  # noqa: BLE001
        log.warning("清理暂存密级失败: %s", e)


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
    last_reconcile = time.monotonic()
    while True:
        try:
            tasks = claim_tasks()
            for t in tasks:
                ok = await process_task(t)
                finish_task(t["id"], ok, error="process failed" if not ok else "")
            # 周期兜底：补齐缺密级/缺索引的档案，保证「扫描完即可检索」
            if time.monotonic() - last_reconcile >= RECONCILE_SECONDS:
                last_reconcile = time.monotonic()
                await reconcile_missing()
                cleanup_pending_classification()
        except Exception:
            log.exception("worker 循环异常")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
