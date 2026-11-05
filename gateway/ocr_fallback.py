"""手写 / 弱印刷 OCR 兜底引擎：PaddleOCR（RapidOCR, ONNX）本地推理。

为什么需要它
------------
Tesseract + OCRmyPDF 只覆盖**印刷体**；中文没有手写模型，线上实测：
用户一张黑字白底、人眼完全可读的房源手写笔记，Tesseract 输出 0 行。
本模块作为兜底——当 Paperless 主 OCR（Tesseract）结果为空时，用 PaddleOCR
对原件重新识别并写回 content，让名片、手写板、潦草笔记也能被检索命中。

设计
----
- 纯本地：模型随 rapidocr-onnxruntime 包内置，不联网、不出网、不需密钥。
- 懒加载 + 线程锁：RapidOCR 首次实例化约 0.5s，在线程池（to_thread）推理。
- 输入是档案原件二进制（图片或 PDF）：PDF 用 pypdfium2 逐页渲染，图片用 cv2 解码。
- 合并按页按行、去重，返回纯文本。
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import List, Optional

import cv2
import numpy as np
import pypdfium2 as pdfium

log = logging.getLogger("gateway.ocr_fallback")

_ENGINE: Optional[object] = None
_ENGINE_LOCK = threading.Lock()


def _get_engine():
    """进程内单例：首次实例化下载/加载内置 ONNX 模型。"""
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                from rapidocr_onnxruntime import RapidOCR

                log.info("初始化 PaddleOCR(RapidOCR) 引擎…")
                _ENGINE = RapidOCR()
    return _ENGINE


def _decode_to_pages(data: bytes) -> List[np.ndarray]:
    """档案原件二进制 → BGR ndarray 列表（PDF 逐页渲染；图片单页）。"""
    # 魔数判定 PDF
    if data[:5].startswith(b"%PDF") or data[:4] == b"%PDF":
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as e:  # noqa: BLE001
            log.warning("PDF 渲染失败: %s", e)
            return []
        pages: List[np.ndarray] = []
        for i in range(len(pdf)):
            pil = pdf[i].render(scale=2.0).to_pil().convert("RGB")
            pages.append(cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR))
        return pages
    # 图片：jpeg/png/heic 等（HEIC 已在上游网关转 jpeg）
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    return [img]


def _ocr_once(img: np.ndarray) -> List[str]:
    """单页推理，返回非空文本行列表。"""
    engine = _get_engine()
    result, _ = engine(img)
    if not result:
        return []
    lines: List[str] = []
    for item in result:
        # item: [box(4点), text, score]
        text = item[1] if len(item) > 1 else ""
        if text and text.strip():
            lines.append(text.strip())
    return lines


def recognize_bytes(data: bytes) -> str:
    """对一份档案原件（图片或 PDF 二进制）做 PaddleOCR，返回合并文本。"""
    pages = _decode_to_pages(data)
    if not pages:
        return ""
    all_lines: List[str] = []
    seen: set[str] = set()
    for img in pages:
        try:
            for line in _ocr_once(img):
                if line not in seen:
                    seen.add(line)
                    all_lines.append(line)
        except Exception as e:  # noqa: BLE001
            log.warning("单页 OCR 失败: %s", e)
            continue
    return "\n".join(all_lines)


async def recognize_document_bytes(data: bytes) -> str:
    """异步包装：在默认线程池跑同步推理，避免阻塞事件循环。"""
    return await asyncio.to_thread(recognize_bytes, data)
