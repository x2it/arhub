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

import cv2
import numpy as np


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


def enhance_bytes(data: bytes, mode: str = "enhance") -> bytes:
    """主入口：bytes → 增强后 JPEG bytes。

    mode: enhance（裁边+校正+去阴影+CLAHE）/ gray（同上转灰度）/
          bw（同上 Otsu 二值）/ orig（仅裁边+校正）
    """
    if mode not in ("enhance", "gray", "bw", "orig"):
        raise ValueError(f"未知增强模式: {mode}")
    buf = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片，仅支持 JPG/PNG")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    contour = _find_document_contour(gray)
    if mode == "enhance":
        work = _perspective_crop(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), contour)
        work = _remove_shadow(work)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        work = clahe.apply(work)
    elif mode == "gray":
        work = _perspective_crop(gray, contour)
        work = _remove_shadow(work)
    elif mode == "bw":
        work = _perspective_crop(gray, contour)
        work = _remove_shadow(work)
        work = cv2.threshold(work, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    else:  # orig：仅几何校正
        work = _perspective_crop(img, contour)
    ok, out = cv2.imencode(".jpg", work, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise ValueError("JPEG 编码失败")
    return out.tobytes()
