"""检索质量与采集质量回归测试。

锁住三个线上暴露过的真实缺陷：
1. 语义检索无相似度阈值 → 库里文档少时「搜什么都返回全库」；
2. 同一档案多个分块重复占用 top_k；
3. 翻拍屏幕 / 文字过淡的低对比度图被「去阴影」洗掉笔画，OCR 返回空，
   且增强产物不带 DPI 元数据导致 ocrmypdf 报 `'dpi'` 警告。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _unit_vectors(scores: list[float], dim: int = 512) -> np.ndarray:
    """构造与 q=e0 的余弦相似度恰为 scores 的一组单位向量。"""
    q = np.zeros(dim, dtype=np.float32)
    q[0] = 1.0
    vecs = []
    for i, sc in enumerate(scores):
        v = np.zeros(dim, dtype=np.float32)
        v[0] = sc
        v[(i % (dim - 1)) + 1] = np.sqrt(max(0.0, 1.0 - sc * sc))
        vecs.append(v)
    return q, np.stack(vecs)


def test_semantic_threshold_rejects_unrelated_query():
    from semantic.matrix import ResidentMatrix

    q, mat = _unit_vectors([0.384, 0.372, 0.301, 0.271])
    metas = [{"doc_id": i + 1, "doc_title": f"D{i}", "text": "t", "chunk_index": 0}
             for i in range(4)]
    m = ResidentMatrix()
    m.replace(metas, mat)
    # 无关查询实测 top1 仅 0.38，0.40 阈值必须全部挡掉
    assert m.search(q, top_k=10, min_score=0.40, rel_margin=0.18) == []


def test_semantic_threshold_keeps_relevant_hits():
    from semantic.matrix import ResidentMatrix

    q, mat = _unit_vectors([0.69, 0.45, 0.32])
    metas = [{"doc_id": i + 1, "doc_title": f"D{i}", "text": "t", "chunk_index": 0}
             for i in range(3)]
    m = ResidentMatrix()
    m.replace(metas, mat)
    hits = m.search(q, top_k=10, min_score=0.40, rel_margin=0.18)
    assert [h["doc_id"] for h in hits] == [1]
    assert hits[0]["score"] == pytest.approx(0.69, abs=1e-3)


def test_semantic_dedupes_chunks_of_same_document():
    from semantic.matrix import ResidentMatrix

    # doc_id=1 的两个分块都高分，原型会把同一档案列出两次
    q, mat = _unit_vectors([0.62, 0.58, 0.55])
    metas = [
        {"doc_id": 1, "doc_title": "A", "text": "chunk-0", "chunk_index": 0},
        {"doc_id": 1, "doc_title": "A", "text": "chunk-1", "chunk_index": 1},
        {"doc_id": 2, "doc_title": "B", "text": "chunk-0", "chunk_index": 0},
    ]
    m = ResidentMatrix()
    m.replace(metas, mat)
    hits = m.search(q, top_k=10, min_score=0.40, rel_margin=0.18)
    assert [h["doc_id"] for h in hits] == [1, 2]
    assert len(hits) == len({h["doc_id"] for h in hits})
    assert hits[0]["chunk_index"] == 0  # 保留最高分分块


def test_quality_flag_detects_screen_photo_but_not_normal_page():
    import cv2

    from gateway.scan_enhance import quality_flag

    # 正常扫描件：白底 + 少量深色笔画，中间灰占比极低
    normal = np.full((600, 400), 240, np.uint8)
    cv2.putText(normal, "TEST DOC", (30, 300), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, 30, 2)
    assert quality_flag(normal) == "ok"

    # 翻拍屏幕：浅灰文字 + 摩尔纹，中间灰(140~205)占比显著
    screen = np.full((600, 400), 225, np.uint8)
    cv2.putText(screen, "TEST DOC", (30, 300), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, 175, 2)
    for y in range(0, 600, 3):
        screen[y:y + 1, :] = 190
    assert quality_flag(screen) == "low-contrast"

    # 纯空白页不能误判为低对比度（另有 is_blank 负责）
    assert quality_flag(np.full((600, 400), 250, np.uint8)) == "ok"


def test_enhance_full_returns_quality_and_writes_dpi():
    import cv2

    from gateway.scan_enhance import enhance_bytes_full

    img = np.full((600, 400), 235, np.uint8)
    cv2.putText(img, "ARCHIVE", (30, 300), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, 40, 2)
    ok, jpg = cv2.imencode(".jpg", img)
    assert ok
    out, blank, quality = enhance_bytes_full(jpg.tobytes(), "enhance")
    assert isinstance(blank, bool) and quality in ("ok", "low-contrast")
    assert out[:2] == b"\xff\xd8"  # 合法 JPEG
    # 写入 300 DPI，消除 ocrmypdf 的 "Error while getting DPI from image: 'dpi'"
    pil = Image.open(io.BytesIO(out))
    assert pil.info.get("dpi") == (300, 300)


def test_enhance_meta_compat_keeps_two_value_tuple():
    import cv2

    from gateway.scan_enhance import enhance_bytes, enhance_bytes_meta

    ok, jpg = cv2.imencode(".jpg", np.full((300, 200), 240, np.uint8))
    assert ok
    out, blank = enhance_bytes_meta(jpg.tobytes(), "gray")
    assert isinstance(out, bytes) and isinstance(blank, bool)
    assert isinstance(enhance_bytes(jpg.tobytes(), "orig"), bytes)
