"""在线预览渲染、下载 MIME 与分页契约的回归测试。

锁住三类真实故障：
1. 浏览器 PDF 插件不可用 → 后端必须能把 PDF 逐页渲染成 JPEG；
2. 原件是 JPEG 却被标成 application/pdf → 下载/预览 MIME 必须按魔数判定；
3. 「只能显示 50 条」→ 浏览模式契约里必须有真实总数与分页字段。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gateway import imaging, preview  # noqa: E402
from gateway.schemas import SearchHit, SearchResponse  # noqa: E402


def make_pdf(pages: int = 3) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(420, 595))
    for i in range(pages):
        c.setFont("Helvetica", 18)
        c.drawString(60, 500, f"ArcHub page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_jpeg(w: int = 800, h: int = 1100) -> bytes:
    im = Image.new("RGB", (w, h), (240, 238, 232))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def make_multiframe_tiff(n: int = 3) -> bytes:
    ims = [Image.new("RGB", (300, 400), (200 + i * 10, 230, 240)) for i in range(n)]
    buf = io.BytesIO()
    ims[0].save(buf, "TIFF", save_all=True, append_images=ims[1:])
    return buf.getvalue()


def test_pdf_page_count_and_render():
    data = make_pdf(3)
    assert preview.mime_of(data) == "application/pdf"
    assert preview.ext_of(data) == "pdf"
    for idx in range(3):
        jpg, count = preview.render_pdf(data, idx)
        assert count == 3
        assert jpg[:3] == b"\xff\xd8\xff"  # 真 JPEG，不是 PDF
        im = Image.open(io.BytesIO(jpg))
        assert im.width <= preview.MAX_WIDTH
        assert im.height > 0


def test_pdf_page_out_of_range():
    data = make_pdf(2)
    with pytest.raises(ValueError):
        preview.render_pdf(data, 2)
    with pytest.raises(ValueError):
        preview.render_pdf(data, -1)


def test_render_source_image():
    jpg, count = preview.render_source(make_jpeg(), 0)
    assert count == 1
    assert jpg[:3] == b"\xff\xd8\xff"


def test_render_source_multiframe_tiff():
    tif = make_multiframe_tiff(3)
    assert len(imaging.iter_frames(tif)) == 3
    jpg, count = preview.render_source(tif, 2)
    assert count == 3
    assert jpg[:3] == b"\xff\xd8\xff"


def test_exif_orientation_is_applied():
    """竖拍图带 Orientation=6 时，预览宽度应大于高度（方向已校正）。"""
    im = Image.new("RGB", (400, 800), (250, 248, 240))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6  # Orientation: 需顺时针旋转 90°
    im.save(buf, "JPEG", exif=exif)
    jpg, _ = preview.render_source(buf.getvalue(), 0)
    out = Image.open(io.BytesIO(jpg))
    assert out.width > out.height


def test_mime_by_magic_not_extension():
    # 原件是 JPEG 但后端曾硬编码 application/pdf，这里锁住真实类型
    assert preview.mime_of(make_jpeg()) == "image/jpeg"
    assert preview.ext_of(make_jpeg()) == "jpg"
    assert preview.mime_of(b"%PDF-1.7 x") == "application/pdf"
    assert preview.mime_of(b"GIF89a....") == "image/gif"


def test_unknown_source_rejected():
    with pytest.raises(ValueError):
        preview.render_source(b"not an image at all", 0)


def test_browse_paging_contract():
    """浏览模式 total 是库内可见总数，分页字段随响应一起返回。"""
    r = SearchResponse(
        request_id="x", mode="hybrid", total=137, browsing=True,
        page=2, page_size=20, total_pages=7, has_more=True,
        results=[SearchHit(doc_id=1, source="browse")])
    d = r.model_dump()
    assert d["total"] == 137          # 不是本页 20 条
    assert d["page"] == 2
    assert d["total_pages"] == 7
    assert d["has_more"] is True
