"""通用原件预览渲染层：把任意原件 / 归档版统一渲染成 JPEG 页。

为什么需要这一层
----------------
浏览器内置 PDF 阅读器在移动端（Edge / 微信 WebView / 各家国产浏览器）常被
禁用，或把 PDF 直接当下载处理；``<iframe src=*.pdf>`` 因此无法可靠预览，
用户只会看到「已在 Microsoft Edge 中禁用 PDF 阅读器」和一个下载按钮。

这里不依赖任何浏览器插件：后端用 PyMuPDF 把 PDF 每一页渲染成 JPEG，图片类
原件则复用 imaging 层（EXIF 校正 / 透明底压白 / 多帧展开）转成 JPEG，前端
只用 ``<img>`` 展示。由此同时获得：

- 多页文档可翻页（多页 TIFF / 多页 PDF / 归档 PDF）
- 原件与归档版可切换（保留拍摄原貌 vs 带 OCR 文本层的校正版）
- 空 OCR 文档依然能看到原图本身（翻拍屏幕、空白页一眼可辨）
- 预览走统一鉴权请求层（三通道 JWT + 401 统一登出），不再依赖裸 cookie

PDFium 绑定（pypdfium2）是纯 wheel，不需要在镜像里安装系统 poppler，适合
离线私有化部署；且为 BSD-3-Clause / Apache-2.0，与本项目 Apache-2.0 兼容
（PyMuPDF 是 AGPL-3.0，开源分发有合规风险，故不采用）。
"""
from __future__ import annotations

import io
import logging
from typing import Tuple

import pypdfium2 as pdfium
from PIL import Image

from gateway import imaging

log = logging.getLogger("gateway.preview")

MAX_WIDTH = 1600                      # 预览长边上限：兼顾清晰度与移动端流量
JPEG_QUALITY = 84
MAX_PAGES = 300                       # 单文档预览页数上限，防超大 PDF 拖垮服务
MAX_SOURCE_BYTES = 80 * 1024 * 1024  # 预览回源体积上限


def _fit(im: Image.Image) -> Image.Image:
    im = im.convert("RGB")
    if im.width > MAX_WIDTH:
        h = max(1, round(im.height * MAX_WIDTH / im.width))
        im = im.resize((MAX_WIDTH, h), Image.LANCZOS)
    return im


def _jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    _fit(im).save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def render_pdf(pdf_bytes: bytes, page_index: int) -> Tuple[bytes, int]:
    """渲染 PDF 第 ``page_index`` 页（0-based）→ ``(JPEG bytes, 总页数)``。"""
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"无法解析 PDF：{e}") from e
    try:
        count = len(pdf)
        if count < 1:
            raise ValueError("PDF 不含任何页面")
        if not 0 <= page_index < count:
            raise ValueError(f"页码越界（{page_index + 1}/{count}）")
        shown = min(count, MAX_PAGES)
        if count > MAX_PAGES:
            log.warning("PDF 共 %s 页，预览仅提供前 %s 页", count, MAX_PAGES)
        page = pdf[page_index]
        # 以「目标宽度 1600px」反推渲染比例，避免大页面产出超大位图
        zoom = min(2.6, max(0.4, MAX_WIDTH / max(1.0, page.get_size()[0])))
        bitmap = page.render(scale=zoom)
        im = bitmap.to_pil().convert("RGB")
        return _jpeg(im), shown
    finally:
        pdf.close()


def render_source(data: bytes, page_index: int) -> Tuple[bytes, int]:
    """任意原件 / 归档字节 → ``(JPEG bytes, 页数)``。

    - PDF：PyMuPDF 逐页渲染；
    - 图片：imaging.iter_frames 展开（多页 TIFF / GIF 每帧即一页）。
    """
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("文件过大，无法在线预览，请直接下载")
    kind = imaging.sniff(data)
    if kind == imaging.PDF:
        return render_pdf(data, page_index)
    if kind == imaging.UNKNOWN:
        raise ValueError("无法识别的原件格式，请直接下载")
    frames = imaging.iter_frames(data)
    count = len(frames)
    if not 0 <= page_index < count:
        raise ValueError(f"页码越界（{page_index + 1}/{count}）")
    return _jpeg(frames[page_index]), min(count, MAX_PAGES)


def mime_of(data: bytes) -> str:
    """按魔数返回真实 MIME —— 下载端点不能把 JPEG 原件标成 application/pdf。"""
    kind = imaging.sniff(data)
    return {
        imaging.PDF: "application/pdf",
        imaging.JPEG: "image/jpeg",
        imaging.PNG: "image/png",
        imaging.WEBP: "image/webp",
        imaging.TIFF: "image/tiff",
        imaging.BMP: "image/bmp",
        imaging.GIF: "image/gif",
        imaging.HEIF: "image/heic",
    }.get(kind, "application/octet-stream")


def ext_of(data: bytes) -> str:
    kind = imaging.sniff(data)
    return {
        imaging.PDF: "pdf", imaging.JPEG: "jpg", imaging.PNG: "png",
        imaging.WEBP: "webp", imaging.TIFF: "tiff", imaging.BMP: "bmp",
        imaging.GIF: "gif", imaging.HEIF: "heic",
    }.get(kind, "bin")
