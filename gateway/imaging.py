"""图片归一化层：格式嗅探 → EXIF 方向校正 → Paperless 可消费格式。

这一层存在的三个原因（都是"扫进去了但 OCR 不出 / 方向颠倒"的真实故障源）：

1. **EXIF Orientation 被丢弃**
   手机照片普遍带 EXIF Orientation 标记，而 OpenCV 的 ``cv2.imdecode`` 直接
   忽略该标记——竖着拍的文档在增强与 OCR 阶段会整体旋转 90°。
   扫描全能王与微软 Lens 都会处理这一点，这里用 Pillow 的
   ``ImageOps.exif_transpose`` 统一校正。

2. **Paperless 只认有限格式**
   Paperless-ngx 经由 img2pdf / OCRmyPDF 消费文件，实际可用的只有
   PDF / JPEG / PNG / TIFF（单页）。iPhone 默认的 HEIC、浏览器的 WebP、
   截图 BMP、动图 GIF 若直接透传，会在消费阶段失败或被静默丢弃。

3. **多页 TIFF 只入第一页**
   TIFF 是多页容器格式，透传给 Paperless 只会留下首页。
   这里把多页 TIFF 展开为多页 PDF，其余页完整保留。

设计约束：
- **确定性**：同样输入必须产出同样字节，否则上传幂等键（内容 SHA-256）会漂移。
  因此不需要变换的格式（PDF、无旋转标记的 JPEG/PNG）一律**原样透传**，
  只在确有必要时重编码。
- **不放大**：HEIC→JPEG 走 q92，PNG 保持无损，避免归档体积膨胀。
- **单页 TIFF 也转 JPEG**：Paperless 对 TIFF 的多页支持依赖 OCRmyPDF 行为，
  统一转 JPEG 后行为可预期。
"""
from __future__ import annotations

import io
from typing import Tuple

import numpy as np
from PIL import Image, ImageOps

# HEIC/HEIF（iPhone 默认格式）需要 libheif；pillow-heif 为 manylinux 自带二进制。
# 缺失时降级为"不支持该格式"，不影响其余链路。
try:  # pragma: no cover - 取决于运行环境是否安装 pillow-heif
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # pragma: no cover
    HEIF_SUPPORTED = False

# Pillow 默认的解压炸弹阈值约为 89M 像素，这里显式收紧到 80M。
Image.MAX_IMAGE_PIXELS = 80_000_000

PDF = "pdf"
JPEG = "jpeg"
PNG = "png"
WEBP = "webp"
TIFF = "tiff"
BMP = "bmp"
GIF = "gif"
HEIF = "heif"
UNKNOWN = "unknown"

# Paperless / OCRmyPDF 能直接消费的格式
PAPERLESS_NATIVE = (PDF, JPEG, PNG)

SUPPORTED_LABEL = "PDF / JPG / PNG / WEBP / TIFF / BMP / GIF / HEIC"


def sniff(head: bytes) -> str:
    """按魔数判定图片格式（不看扩展名——扩展名可伪造且各浏览器不一致）。"""
    if not head:
        return UNKNOWN
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return WEBP
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return TIFF
    if head.startswith(b"BM"):
        return BMP
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return GIF
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (
            b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
            b"hevm", b"hevs", b"mif1", b"msf1", b"avif",
        ):
            return HEIF
    return UNKNOWN


def _needs_rotation(img: Image.Image) -> bool:
    """EXIF Orientation 是否指示需要旋转（1 = 正常）。

    必须在旋转**之前**判断——旋转后的图像 orientation 已被清掉。
    """
    try:
        orientation = int(img.getexif().get(0x0112, 1))
    except Exception:
        return False
    return orientation not in (0, 1)


def _transpose(img: Image.Image) -> Image.Image:
    """按 EXIF Orientation 校正方向（失败则原样返回）。"""
    try:
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def _flatten(img: Image.Image) -> Image.Image:
    """去掉 alpha 通道（透明区域压白底），否则转 JPEG 会出现黑块。"""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _frames(img: Image.Image) -> list[Image.Image]:
    """展开多帧容器（TIFF / GIF）为逐页列表。

    注意：必须在**未旋转**的原图上取帧——``ImageOps.exif_transpose`` 走的是
    ``copy()``，会把多帧信息压成单帧，导致多页 TIFF 只剩首页。
    """
    n = int(getattr(img, "n_frames", 1) or 1)
    if n <= 1:
        return [img]
    out: list[Image.Image] = []
    for i in range(n):
        try:
            img.seek(i)
        except EOFError:
            break
        out.append(img.copy())
    return out or [img]


def open_image(data: bytes) -> Image.Image:
    """解码为 Pillow 图像（不做任何变换，保留多帧与 EXIF 元信息）。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ValueError(f"无法解码图片：{exc}") from exc
    return img


def decode_pil(data: bytes) -> Image.Image:
    """解码为 Pillow 图像，并完成 EXIF 方向校正。"""
    return _transpose(open_image(data))


def decode_bgr(data: bytes) -> np.ndarray:
    """解码为 OpenCV 风格的 BGR ndarray（供增强管线使用）。"""
    img = _flatten(decode_pil(data))
    arr = np.asarray(img)
    return arr[:, :, ::-1].copy()


def to_jpeg(img: Image.Image, quality: int = 92) -> bytes:
    """转 JPEG 字节（白色底 flattening 已处理）。"""
    buf = io.BytesIO()
    _flatten(img).save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def to_png(img: Image.Image) -> bytes:
    """转 PNG 字节（保留无损）。"""
    buf = io.BytesIO()
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def to_pdf(images: list[Image.Image]) -> bytes:
    """多页图像 → 单个多页 PDF（Pillow 以 JPEG 编码写入）。"""
    if not images:
        raise ValueError("没有可写入的页面")
    pages = [_flatten(im) for im in images]
    buf = io.BytesIO()
    pages[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=200.0,
    )
    return buf.getvalue()


def strip_ext(filename: str) -> str:
    """去掉扩展名，保留主名（用于生成规范文件名）。"""
    name = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem or "document"


def normalize_upload(payload: bytes, filename: str) -> Tuple[bytes, str, str]:
    """把任意上传文件归一化为 (数据, Content-Type, 扩展名)。

    - PDF 原样透传（幂等键与既有归档保持一致）
    - JPEG / PNG 无旋转标记时原样透传（避免不必要的重编码与体积膨胀）
    - 带 EXIF 旋转的 JPEG / PNG 校正方向后重编码
    - 多页 TIFF → 多页 PDF；单页 TIFF → JPEG
    - WEBP / BMP / GIF / HEIC → JPEG
    """
    kind = sniff(payload[:32])
    if kind == PDF:
        return payload, "application/pdf", "pdf"
    if kind == UNKNOWN:
        raise ValueError(f"仅支持 {SUPPORTED_LABEL} 文件")

    img = open_image(payload)

    if kind in (JPEG, PNG):
        if not _needs_rotation(img):
            # 无需变换 → 原样透传（保证上传幂等键稳定，且不损失画质）
            return payload, ("image/jpeg" if kind == JPEG else "image/png"), (
                "jpg" if kind == JPEG else "png"
            )
        rotated = _transpose(img)
        if kind == JPEG:
            return to_jpeg(rotated), "image/jpeg", "jpg"
        return to_png(rotated), "image/png", "png"

    if kind == TIFF:
        pages = _frames(img)
        if len(pages) > 1:
            return to_pdf([_transpose(p) for p in pages]), "application/pdf", "pdf"
        return to_jpeg(_transpose(pages[0])), "image/jpeg", "jpg"

    # WEBP / BMP / GIF / HEIF：统一转 JPEG
    return to_jpeg(_transpose(img)), "image/jpeg", "jpg"
