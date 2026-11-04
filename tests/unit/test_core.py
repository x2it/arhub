"""核心逻辑单元测试（CI 门禁）：RRF/切片/矩阵/扫描增强/fail-closed/JWT。"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------- RRF 融合 ----------
def test_rrf_fuse_merges_and_ranks():
    from gateway.app import rrf_fuse

    kw = [{"doc_id": 1, "title": "A", "source": "keyword", "highlights": "<mark>x</mark>"},
          {"doc_id": 2, "title": "B", "source": "keyword"}]
    sm = [{"doc_id": 2, "title": "B", "source": "semantic", "snippet": "s", "chunk_index": 0},
          {"doc_id": 1, "title": "A", "source": "semantic", "chunk_index": 1}]
    fused = rrf_fuse(kw, sm, top_k=5)
    assert len(fused) == 2
    by_id = {r["doc_id"]: r for r in fused}
    assert set(by_id[1]["matched_by"]) == {"keyword", "semantic"}
    assert by_id[2]["snippet"] == "s"
    assert by_id[2]["chunk_index"] == 0
    # 双路同时命中的得分应高于单路
    assert all(r["rrf_score"] > 1 / 61 for r in fused)


def test_rrf_fuse_respects_top_k():
    from gateway.app import rrf_fuse

    lists = [[{"doc_id": i, "source": "keyword"} for i in range(20)]]
    assert len(rrf_fuse(*lists, top_k=5)) == 5


# ---------- 中文切片 ----------
def test_split_text_sentence_boundaries_and_overlap():
    from semantic.indexer import split_text

    text = "。".join(f"第{i}句话内容" for i in range(100)) + "。"
    chunks = split_text(text, size=50, overlap=10)
    assert len(chunks) > 3
    assert all(len(c) <= 55 for c in chunks)


def test_split_text_empty():
    from semantic.indexer import split_text

    assert split_text("") == []


# ---------- 常驻矩阵 ----------
def test_resident_matrix_cow_and_topk():
    from semantic.matrix import ResidentMatrix

    m = ResidentMatrix()
    rng = np.random.default_rng(42)
    mat = rng.random((1000, 64)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    metas = [{"doc_id": i, "text": f"c{i}", "chunk_index": i} for i in range(1000)]
    m.replace(metas, mat)
    hits = m.search(mat[42].copy(), top_k=5)
    assert hits[0]["doc_id"] == 42
    assert hits[0]["score"] > 0.999
    assert hits[0]["chunk_index"] == 42
    assert len(m.search(mat[42].copy(), top_k=3)) == 3


def test_resident_matrix_empty():
    from semantic.matrix import ResidentMatrix

    m = ResidentMatrix()
    assert m.search(np.zeros(8, dtype=np.float32), 5) == []
    assert not m.ready


# ---------- 扫描增强（对齐扫描全能王）----------
def test_scan_enhance_all_modes():
    import cv2

    from gateway.scan_enhance import enhance_bytes

    img = np.full((600, 400), 255, np.uint8)
    cv2.putText(img, "TEST DOC", (40, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 30, 2)
    grad = np.tile(np.linspace(120, 255, 400).astype(np.uint8), (600, 1))
    shadowed = cv2.multiply(img, grad, scale=1.0 / 255)
    rot = cv2.warpAffine(shadowed,
                         cv2.getRotationMatrix2D((200, 300), 4, 1.0), (400, 600))
    ok, jpg = cv2.imencode(".jpg", rot)
    assert ok
    for mode in ("enhance", "gray", "bw", "orig"):
        out = enhance_bytes(jpg.tobytes(), mode)
        assert out[:2] == b"\xff\xd8" and len(out) > 1000


def test_scan_enhance_rejects_bad_mode_and_bytes():
    from gateway.scan_enhance import enhance_bytes

    with pytest.raises(ValueError):
        enhance_bytes(b"x", "bogus")
    with pytest.raises(ValueError):
        enhance_bytes(b"not-an-image", "enhance")


def test_blank_page_detection():
    import cv2

    from gateway.scan_enhance import is_blank

    blank = np.full((300, 300), 250, np.uint8)
    assert is_blank(blank)
    inked = blank.copy()
    cv2.putText(inked, "Hello", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, 0, 3)
    assert not is_blank(inked)


# ---------- fail-closed 密级过滤 ----------
def test_post_filter_fail_closed(monkeypatch):
    import gateway.deps as deps

    monkeypatch.setattr(deps, "load_doc_security",
                        lambda ids: {1001: {"classification": 3, "owner_dept": None}})
    u = deps.UserCtx(id=9, username="viewer", roles=["viewer"], clearance=1)
    # 1001 密级 3 > clearance 1 → 拒；1002 无密级记录 → fail-closed 拒
    assert deps.post_filter_hits(u, [{"doc_id": 1001}, {"doc_id": 1002}]) == []


def test_post_filter_allows_within_clearance(monkeypatch):
    import gateway.deps as deps

    monkeypatch.setattr(deps, "load_doc_security",
                        lambda ids: {1002: {"classification": 0, "owner_dept": None}})
    u = deps.UserCtx(id=9, username="viewer", roles=["viewer"], clearance=1)
    kept = deps.post_filter_hits(u, [{"doc_id": 1002}])
    assert [h["doc_id"] for h in kept] == [1002]
    assert kept[0]["classification"] == 0


# ---------- JWT ----------
def test_jwt_roundtrip():
    import os

    os.environ.setdefault("JWT_SECRET", "test-secret-for-ci")
    from gateway import security

    token, exp = security.create_access_token(7, "alice", ["admin"], 3)
    claims = security.decode_token(token)
    assert claims["sub"] == "7" and claims["roles"] == ["admin"] and claims["clearance"] == 3
    assert security.decode_token("tampered") is None


def test_password_hash():
    import os

    os.environ.setdefault("JWT_SECRET", "test-secret-for-ci")
    from gateway.security import hash_password, verify_password

    h = hash_password("s3cret")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
