"""扫描增强管线：对齐扫描全能王 / 微软 Lens 的核心图像能力（服务端 OpenCV）。

对齐能力：
- 文档边缘自动检测 + 裁边（Canny + 最大四边形轮廓）
- 透视校正（四点变换压平纸张）
- 去阴影（高斯背景估计 + 除法归一化）
- 对比度增强（CLAHE 限制对比度自适应直方图均衡）
- 增强模式：enhance / gray / bw / orig
- 空白页检测（供批量扫描剔除）

超越点：增强输出直接进入 OCR → 档号著录 → 密级 → 全文+语义检索 → 审计 API
全链路，这是消费级扫描 App 不具备的档案级能力。
"""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from gateway.imaging import decode_bgr


def _order_points(pts: np.ndarray) -> np.ndarray:
    """tl, tr, br, bl。"""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]
    rect[3] = pts[np.argmax(d)]
    return rect


def _find_document_contour(gray: np.ndarray):
    """找最大四边形文档轮廓；找不到返回 None（不裁边，保守处理）。"""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    page_area = gray.shape[0] * gray.shape[1]
    best = None
    best_area = 0
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(c)
        if area < page_area * 0.15:  # 太小视为噪音
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and area > best_area:
            best, best_area = approx.reshape(4, 2), area
    return best


def _perspective_crop(img: np.ndarray, contour: np.ndarray | None) -> np.ndarray:
    if contour is None:
        return img
    rect = _order_points(contour.astype(np.float32))
    (tl, tr, br, bl) = rect
    w = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
    h = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))
    if w < 50 or h < 50 or w * h < img.shape[0] * img.shape[1] * 0.1:
        return img  # 校正后面积太小 → 轮廓误检，放弃裁边
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(img, m, (w, h))


def _remove_shadow(gray: np.ndarray) -> np.ndarray:
    """背景估计除法：大核高斯近似光照背景，相除归一化后去阴影/光照不均。"""
    bg = cv2.medianBlur(gray, 21)
    bg = cv2.GaussianBlur(bg, (0, 0), 15)
    norm = cv2.divide(gray, bg, scale=255)
    return norm


def is_blank(gray: np.ndarray, thresh: float = 0.995) -> bool:
    """空白页检测：非白像素占比过低。"""
    binary = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)[1]
    ink = float(np.count_nonzero(binary)) / binary.size
    return ink < (1 - thresh)


def quality_flag(gray: np.ndarray) -> str:
    """识别「翻拍屏幕 / 文字过淡」这类 OCR 高风险图。

    翻拍屏照片上文字与背景都是浅灰，还夹着摩尔纹，中间灰（140~205）像素
    占比极高（实测 0.19）；正常扫描件中间灰几乎为 0（0.0005），空白页为 0。
    用中间灰占比而非均值来判定，才不会把正常白纸文档误判成低质量。

    这类图若走「去阴影（背景除法）+ 强 CLAHE」，浅色笔画会被一起归一化掉，
    实测 OCR 直接返回 ``No text was found``，最终入库一份空文档。
    """
    mean = float(gray.mean())
    mid = float(np.count_nonzero((gray >= 140) & (gray <= 205))) / gray.size
    return "low-contrast" if mean > 190 and mid > 0.05 else "ok"


def enhance_bytes(data: bytes, mode: str = "enhance") -> bytes:
    """主入口：bytes → 增强后 JPEG bytes。

    mode: enhance（裁边+校正+去阴影+CLAHE）/ gray（同上转灰度）/
          bw（同上 Otsu 二值）/ orig（仅裁边+校正）

    解码走 imaging.decode_bgr：统一处理 HEIC/WebP/TIFF 等格式，
    并按 EXIF Orientation 校正方向（cv2.imdecode 会忽略它，导致竖拍图
    在 OCR 前整体旋转 90°）。
    """
    return enhance_bytes_full(data, mode)[0]


def enhance_bytes_meta(data: bytes, mode: str = "enhance") -> tuple[bytes, bool]:
    """兼容入口：返回 (JPEG bytes, 是否空白页)。"""
    out, blank, _ = enhance_bytes_full(data, mode)
    return out, blank


def enhance_bytes_full(data: bytes, mode: str = "enhance") -> tuple[bytes, bool, str]:
    """增强并返回 (JPEG bytes, 是否空白页, 图像质量标记)。

    - 空白页判定让批量扫描自动剔除误触的空白照片。
    - 质量标记 ``low-contrast`` 表示疑似翻拍屏幕 / 文字过淡，此时**跳过**
      去阴影（背景除法）并改用温和 CLAHE，避免把浅色笔画一起洗掉；
      前端据此提示用户「OCR 可能识别不出，建议重拍或改用原图」。
    """
    if mode not in ("enhance", "gray", "bw", "orig"):
        raise ValueError(f"未知增强模式: {mode}")
    try:
        img = decode_bgr(data)
    except ValueError:
        raise
    except Exception as e:  # 解码层的兜底异常统一转成可读错误
        raise ValueError(f"无法解码图片，仅支持 JPG/PNG/WEBP/TIFF/HEIC：{e}") from e
    if img is None or img.size == 0:
        raise ValueError("图片内容为空")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blank = is_blank(gray)
    quality = quality_flag(gray)
    contour = _find_document_contour(gray)

    def _prep(g: np.ndarray) -> np.ndarray:
        """低对比度图只做温和局部增强；正常图走去阴影。"""
        if quality == "low-contrast":
            return cv2.createCLAHE(clipLimit=1.2, tileGridSize=(8, 8)).apply(g)
        return _remove_shadow(g)

    if mode == "enhance":
        work = _perspective_crop(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), contour)
        work = _prep(work)
        if quality == "ok":
            work = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(work)
    elif mode == "gray":
        work = _prep(_perspective_crop(gray, contour))
    elif mode == "bw":
        work = _prep(_perspective_crop(gray, contour))
        work = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    else:  # orig：仅几何校正
        work = _perspective_crop(img, contour)

    # 用 Pillow 编码以写入 300 DPI：cv2.imencode 产出的 JPEG 不带密度信息，
    # ocrmypdf 会报 "Error while getting DPI from image: 'dpi'" 并按默认分辨率
    # 判断版面，小字文档容易识别失败。
    pil = (Image.fromarray(cv2.cvtColor(work, cv2.COLOR_BGR2RGB))
           if work.ndim == 3 else Image.fromarray(work))
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=88, dpi=(300, 300))
    return buf.getvalue(), blank, quality
