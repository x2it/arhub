"""ocr_fallback（PaddleOCR 手写兜底）单元测试。

用 cv2.putText 生成白底黑字图，不依赖系统字体文件，宿主机与 CI 一致。
验证：印刷体可识别、纯白图返回空、PDF 能按页解码。
"""
import cv2
import numpy as np
import pytest
from gateway.ocr_fallback import recognize_bytes, _decode_to_pages


def _printed_png(text: str) -> bytes:
    img = np.full((200, 820, 3), 255, np.uint8)
    cv2.putText(img, text, (20, 140), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (0, 0, 0), 6, cv2.LINE_AA)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_recognize_printed_text():
    text = recognize_bytes(_printed_png("ARHUB 2026"))
    assert text.strip(), "印刷体英文数字应被 PaddleOCR 识别"
    assert "2026" in text or "ARHUB" in text.upper()


def test_recognize_blank_returns_empty():
    img = np.full((120, 400, 3), 255, np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert recognize_bytes(buf.tobytes()).strip() == ""


def test_decode_pdf_pages():
    try:
        from gateway.imaging import to_pdf
        from PIL import Image
    except ImportError:
        pytest.skip("pypdfium2/imaging 不可用")
    pdf = to_pdf([Image.new("RGB", (200, 200), "white"),
                  Image.new("RGB", (200, 200), "white")])
    pages = _decode_to_pages(pdf)
    assert len(pages) == 2
