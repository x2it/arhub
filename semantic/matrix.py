"""常驻内存向量矩阵：启动一次载入，索引变更 COW 原子替换。

修复原型：semantic/app.py:205-215/:307/:314 每请求全表 SELECT + vstack +
argsort 全排序 → 启动常驻 + argpartition top-k + 读写锁。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from functools import lru_cache
from typing import Optional

import numpy as np

log = logging.getLogger("semantic.matrix")


class ResidentMatrix:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metas: list[dict] = []
        self._mat: Optional[np.ndarray] = None

    def replace(self, metas: list[dict], mat: np.ndarray) -> None:
        with self._lock:  # COW：整体引用原子替换，检索期读到一致快照
            self._metas = metas
            self._mat = mat

    @property
    def ready(self) -> bool:
        return self._mat is not None and len(self._metas) > 0

    @property
    def size(self) -> int:
        return len(self._metas)

    def search(self, qvec: np.ndarray, top_k: int,
               min_score: float = 0.40, rel_margin: float = 0.18) -> list[dict]:
        """近邻检索：相似度双阈值过滤 + 文档级去重。

        两个此前缺失的约束：
        1. **阈值**：原型无差别返回 top_k 个近邻。向量库只有 8 篇文档时，
           连「今天天气怎么样」「asdfghjkl」都会把全库返回，污染 hybrid 融合结果。
           这里用「绝对下限」与「相对最佳命中的落差」共同裁定，弱相关一律丢弃。
        2. **去重**：同一档案被切成多个分块，原型会把同一 doc_id 重复列出，
           挤占 top_k 名额并让结果看起来像一堆重复内容。按 doc_id 保留最高分块。
        """
        with self._lock:
            mat, metas = self._mat, self._metas
        if mat is None or not metas:
            return []
        sims = mat @ qvec  # 向量均已归一化，点积即余弦相似度
        best = float(sims.max()) if sims.size else 0.0
        cutoff = max(min_score, best - rel_margin)
        k = min(top_k, len(metas))
        # argpartition O(N) 取候选，再局部排序（替代原型全量 argsort O(N log N)）
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        by_doc: dict[int, dict] = {}
        for i in idx:
            score = round(float(sims[i]), 4)
            if score < cutoff:
                continue
            rec = {**metas[i], "score": score}
            doc_id = rec.get("doc_id")
            if doc_id not in by_doc or score > by_doc[doc_id]["score"]:
                by_doc[doc_id] = rec
        return sorted(by_doc.values(), key=lambda h: h["score"], reverse=True)[:top_k]


@lru_cache
def get_matrix() -> ResidentMatrix:
    return ResidentMatrix()


async def reload_matrix() -> int:
    """从向量库重新载入常驻矩阵，返回向量数。

    **关键**：`semantic`（API，持有常驻矩阵并对外检索）与 `indexer`（worker，
    负责写向量库）是**两个独立进程**。worker 写完向量后必须由 API 进程自己
    重新载入，否则新档案已入库却「检索不到」——这正是向量常驻架构下最易
    踩的一致性坑。
    """
    from semantic.store import get_store  # 延迟导入，避免与 store 循环依赖

    store = get_store()
    loaded = await asyncio.to_thread(store.load_all)
    matrix = get_matrix()
    if loaded:
        metas, mat = loaded
        matrix.replace(metas, mat)
        log.info("矩阵已刷新: %s 向量", len(metas))
        return len(metas)
    matrix.replace([], np.zeros((0, 512), dtype=np.float32))
    log.info("矩阵已刷新: 0 向量")
    return 0
