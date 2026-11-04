"""常驻内存向量矩阵：启动一次载入，索引变更 COW 原子替换。

修复原型：semantic/app.py:205-215/:307/:314 每请求全表 SELECT + vstack +
argsort 全排序 → 启动常驻 + argpartition top-k + 读写锁。
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import Optional

import numpy as np


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

    def search(self, qvec: np.ndarray, top_k: int) -> list[dict]:
        with self._lock:
            mat, metas = self._mat, self._metas
        if mat is None or not metas:
            return []
        sims = mat @ qvec  # 向量均已归一化，点积即余弦相似度
        k = min(top_k, len(metas))
        # argpartition O(N) 取候选，再局部排序（替代原型全量 argsort O(N log N)）
        idx = np.argpartition(-sims, k - 1)[:k]
        idx = idx[np.argsort(-sims[idx])]
        return [{**metas[i], "score": round(float(sims[i]), 4)} for i in idx]


@lru_cache
def get_matrix() -> ResidentMatrix:
    return ResidentMatrix()
