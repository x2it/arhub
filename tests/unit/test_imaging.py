"""图片归一化层单元测试：格式嗅探 / EXIF 方向 / 多页 TIFF / HEIC。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# 让测试能以源码树方式导入 gateway 包（与 CI 的仓库根目录运行方式一致）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway import imaging  # noqa: E402


def _png_bytes(size=(120, 80), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(120, 80), exif_orientation=None) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, (10, 120, 200))
    if exif_orientation:
        exif = img.getexif()
        exif[0x0112] = exif_orientation
        img.save(buf, format="JPEG", quality=90, exif=exif)
    else:
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _tiff_bytes(pages=2) -> bytes:
    buf = io.BytesIO()
    frames = [Image.new("RGB", (100, 60), (i * 60, 20, 20)) for i in range(pages)]
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def _webp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 60), (30, 90, 160)).save(buf, format="WEBP")
    return buf.getvalue()


def test_sniff_detects_formats():
    assert imaging.sniff(b"%PDF-1.7\n") == imaging.PDF
    assert imaging.sniff(_jpeg_bytes()[:16]) == imaging.JPEG
    assert imaging.sniff(_png_bytes()[:16]) == imaging.PNG
    assert imaging.sniff(_webp_bytes()[:16]) == imaging.WEBP
    assert imaging.sniff(_tiff_bytes()[:16]) == imaging.TIFF
    assert imaging.sniff(b"BM" + b"\x00" * 20) == imaging.BMP
    assert imaging.sniff(b"GIF89a" + b"\x00" * 20) == imaging.GIF
    assert imaging.sniff(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20) == imaging.HEIF
    assert imaging.sniff(b"not an image at all") == imaging.UNKNOWN
    assert imaging.sniff(b"") == imaging.UNKNOWN


def test_pdf_passthrough_unchanged():
    data = b"%PDF-1.7\nfake"
    payload, ctype, ext = imaging.normalize_upload(data, "a.pdf")
    assert payload == data
    assert ctype == "application/pdf"
    assert ext == "pdf"


def test_png_without_rotation_passthrough():
    data = _png_bytes()
    payload, ctype, ext = imaging.normalize_upload(data, "a.png")
    assert payload == data  # 无旋转标记 → 原样透传，保证幂等键稳定
    assert ctype == "image/png"
    assert ext == "png"


def test_jpeg_with_exif_orientation_is_rotated():
    # EXIF orientation=6 表示"顺时针 90°"，校正后宽高应互换
    data = _jpeg_bytes(size=(120, 80), exif_orientation=6)
    payload, ctype, ext = imaging.normalize_upload(data, "a.jpg")
    assert ctype == "image/jpeg"
    assert ext == "jpg"
    img = Image.open(io.BytesIO(payload))
    assert img.size == (80, 120)  # 宽高互换，说明方向已校正


def test_multi_page_tiff_becomes_multipage_pdf():
    data = _tiff_bytes(pages=3)
    payload, ctype, ext = imaging.normalize_upload(data, "a.tiff")
    assert ctype == "application/pdf"
    assert ext == "pdf"
    assert payload.startswith(b"%PDF")
    # Pillow 只能写不能读 PDF，故直接数页面对象，确认多页没有丢
    assert payload.count(b"/Type /Page\n") + payload.count(b"/Type /Page/") >= 3
    assert b"/Count 3" in payload


def test_single_page_tiff_becomes_jpeg():
    data = _tiff_bytes(pages=1)
    payload, ctype, ext = imaging.normalize_upload(data, "a.tiff")
    assert ctype == "image/jpeg"
    assert ext == "jpg"
    assert Image.open(io.BytesIO(payload)).format == "JPEG"


def test_webp_becomes_jpeg():
    data = _webp_bytes()
    payload, ctype, ext = imaging.normalize_upload(data, "a.webp")
    assert ctype == "image/jpeg"
    assert Image.open(io.BytesIO(payload)).format == "JPEG"


def test_bmp_becomes_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), (1, 2, 3)).save(buf, format="BMP")
    payload, ctype, _ = imaging.normalize_upload(buf.getvalue(), "a.bmp")
    assert ctype == "image/jpeg"
    assert Image.open(io.BytesIO(payload)).format == "JPEG"


def test_unknown_rejected():
    with pytest.raises(ValueError):
        imaging.normalize_upload(b"garbage bytes here", "a.txt")


def test_decode_bgr_shape_and_channel_order():
    arr = imaging.decode_bgr(_png_bytes(size=(40, 20), color=(10, 200, 30)))
    assert arr.shape == (20, 40, 3)
    # RGB(10,200,30) → BGR(30,200,10)
    assert arr[0, 0].tolist() == [30, 200, 10]


def test_heic_supported_when_pillow_heif_available():
    """HEIC 是 iPhone 默认格式；装了 pillow-heif 就必须能解码。"""
    if not imaging.HEIF_SUPPORTED:
        pytest.skip("pillow-heif 未安装")
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (5, 6, 7)).save(buf, format="HEIF")
    payload, ctype, ext = imaging.normalize_upload(buf.getvalue(), "a.heic")
    assert ctype == "image/jpeg"
    assert ext == "jpg"


def test_strip_ext():
    assert imaging.strip_ext("photo.jpg") == "photo"
    assert imaging.strip_ext("/tmp/dir/report.PDF") == "report"
    assert imaging.strip_ext("noext") == "noext"
    assert imaging.strip_ext("") == "document"
