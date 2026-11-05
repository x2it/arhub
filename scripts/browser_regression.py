#!/usr/bin/env python3
"""浏览器回归：登录态自愈、采集页链路、移动端排版（横向溢出断言）。

覆盖此前线上暴露的「页面能看但增强 401、且不跳登录」死局：
  1. 过期令牌直接访问 → 路由守卫跳登录；
  2. 请求中途令牌失效 401 → 统一登出跳登录，不再显示误导性的「增强失败 HTTP 401」；
  3. 重新登录后原路返回正在操作的页面；
  4. 桌面 / 手机两种视口下采集、增强、预览全流程无 JS 错误、无横向溢出。

用法：python3 scripts/browser_regression.py
环境：ARC_BASE_URL（默认 http://127.0.0.1:8700）、ARC_OUT_DIR（截图目录）
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.getenv("ARC_BASE_URL", "http://127.0.0.1:8700").rstrip("/")
OUT_DIR = Path(os.getenv("ARC_OUT_DIR", str(ROOT.parent)))
CREDS = ROOT / "data" / "admin_credentials.txt"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  → {detail}" if detail and not cond else ""))


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def fake_token(exp: int) -> str:
    """构造仅用于前端回归的 JWT：前端只看 exp，后端会因签名无效返回 401。"""
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'sub': '1', 'username': 'admin', 'exp': exp})}.invalid-signature"


def credentials() -> tuple[str, str]:
    for line in CREDS.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            u, p = line.split(":", 1)
            return u.strip(), p.strip()
    raise RuntimeError("admin_credentials.txt 缺少凭据")


def make_image() -> Path:
    from PIL import Image, ImageDraw

    path = Path(tempfile.gettempdir()) / "archub-browser-regression.jpg"
    img = Image.new("RGB", (720, 960), (232, 230, 224))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 90, 660, 870], outline=(40, 60, 80), width=3)
    for i, line in enumerate(["档枢 ArcHub", "浏览器回归测试样张", "BROWSER REGRESSION SAMPLE", "2026 / 11 / 05"]):
        d.text((100, 160 + i * 90), line, fill=(20, 45, 75))
    img.save(path, "JPEG", quality=88)
    return path


def make_low_contrast_image() -> Path:
    """模拟翻拍电脑屏幕：浅灰文字 + 密集中间灰条纹（摩尔纹）。"""
    from PIL import Image, ImageDraw

    path = Path(tempfile.gettempdir()) / "archub-low-contrast.jpg"
    img = Image.new("L", (720, 960), 225)
    d = ImageDraw.Draw(img)
    d.text((50, 220), "FAINT TEXT", fill=175)
    for y in range(0, 960, 3):
        d.line([(0, y), (720, y)], fill=190)
    img.convert("RGB").save(path, "JPEG", quality=88)
    return path


def sign_in(page, user: str, pw: str) -> None:
    page.goto(f"{BASE}/#/login", wait_until="networkidle")
    page.fill('input[autocomplete="username"]', user)
    page.fill('input[autocomplete="current-password"]', pw)
    page.locator("form button.primary").click()
    page.wait_for_function(
        "() => location.hash.startsWith('#/') && !location.hash.includes('login')",
        timeout=20000,
    )


def no_overflow(page) -> tuple[bool, str]:
    return page.evaluate(
        "() => { const sw = document.documentElement.scrollWidth, iw = window.innerWidth; "
        "return [sw <= iw + 1, `scrollWidth=${sw} innerWidth=${iw}`] }"
    )


def toast_within_screen(page) -> tuple[bool, str]:
    el = page.locator(".toast")
    if not el.count():
        return True, "无 toast"
    box = el.bounding_box()
    if not box:
        return True, "toast 不可见"
    right = box["x"] + box["width"]
    iw = page.evaluate("window.innerWidth")
    return right <= iw + 1 and box["width"] <= iw, f"toast right={right:.0f} width={box['width']:.0f} innerWidth={iw}"


def shoot(page, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    page.screenshot(path=str(path), full_page=True)
    print(f"  · 截图 {path}")


def run_view(browser, label: str, viewport: dict, mobile: bool, user: str, pw: str, img: Path) -> None:
    print(f"\n=== {label}（{viewport['width']}×{viewport['height']}）===")
    ctx = browser.new_context(
        viewport=viewport,
        device_scale_factor=2 if mobile else 1,
        is_mobile=mobile,
        has_touch=mobile,
    )
    page = ctx.new_page()
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    sign_in(page, user, pw)
    page.goto(f"{BASE}/#/capture", wait_until="networkidle")
    check(f"{label}：采集页双入口可见",
          page.locator("text=拍照扫描").count() > 0 and page.locator("text=选择本机图片").count() > 0)
    nav_count = page.locator(".nav-link").count()
    check(f"{label}：管理员菜单按角色完整渲染", nav_count >= 6, f"实际 {nav_count} 项")
    ok, detail = no_overflow(page)
    check(f"{label}：空状态无横向溢出", ok, detail)
    shoot(page, f"验证-{label}-采集页空状态.png")

    # 选择本机图片（第二个 input：不带 capture）
    page.locator("input[type=file]").nth(1).set_input_files(str(img))
    page.wait_for_function(
        "() => document.querySelectorAll('.tag.ok, .tag.err, .tag.warn').length > 0",
        timeout=45000,
    )
    check(f"{label}：增强返回就绪（非 401 失败）", page.locator(".tag.ok").count() > 0,
          page.locator(".tag.err").first.inner_text() if page.locator(".tag.err").count() else "")
    check(f"{label}：原图/增强预览均渲染", page.locator(".pair img").count() == 2)
    check(f"{label}：批量归档按钮可用",
          page.locator(".submit-btn").is_enabled())
    ok, detail = no_overflow(page)
    check(f"{label}：有图状态无横向溢出", ok, detail)
    ok, detail = toast_within_screen(page)
    check(f"{label}：提示条不超出屏幕", ok, detail)
    shoot(page, f"验证-{label}-原图增强对比.png")
    check(f"{label}：无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
    ctx.close()


def run_auth_recovery(browser, user: str, pw: str, img: Path) -> None:
    print("\n=== 登录态自愈（核心回归）===")
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    page = ctx.new_page()

    # A) 过期令牌直接访问 → 守卫应跳登录，杜绝「页面正常但接口全 401」
    sign_in(page, user, pw)
    page.evaluate("(t) => localStorage.setItem('archive_jwt', t)", fake_token(1))
    page.goto(f"{BASE}/#/capture", wait_until="networkidle")
    page.wait_for_timeout(400)
    check("过期令牌访问采集页自动跳登录", "login" in page.url, page.url)
    shoot(page, "验证-过期令牌自动跳登录.png")

    # B) 请求中途令牌失效（exp 有效但签名无效 → 后端 401）
    #    统一请求层应登出并跳登录，不得出现「增强失败 HTTP 401」这类死局提示
    page.goto(f"{BASE}/#/login", wait_until="networkidle")
    page.evaluate(
        "() => { localStorage.removeItem('archive_jwt'); localStorage.removeItem('archive_user'); }"
    )
    sign_in(page, user, pw)
    page.goto(f"{BASE}/#/capture", wait_until="networkidle")
    page.evaluate("(t) => localStorage.setItem('archive_jwt', t)",
                  fake_token(int(time.time()) + 3600))
    page.locator("input[type=file]").nth(1).set_input_files(str(img))
    page.wait_for_function("() => location.hash.includes('login')", timeout=20000)
    check("增强请求 401 后自动登出并跳登录", "login" in page.url, page.url)
    check("不再出现误导性的「增强失败 HTTP 401」",
          page.locator("text=增强失败 HTTP 401").count() == 0)

    # C) 重新登录后原路返回采集页
    page.fill('input[autocomplete="username"]', user)
    page.fill('input[autocomplete="current-password"]', pw)
    page.locator("form button.primary").click()
    page.wait_for_function("() => location.hash.includes('capture')", timeout=20000)
    check("重新登录后原路返回采集页", "capture" in page.url, page.url)
    ctx.close()


def run_login_layout(browser) -> None:
    """登录页移动端：grid 列曾被固定宽卡片撑成 424px，导致右侧被裁。"""
    print("\n=== 登录页移动端排版 ===")
    ctx = browser.new_context(viewport={"width": 375, "height": 812},
                              device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{BASE}/#/login", wait_until="networkidle")
    page.wait_for_timeout(300)
    g = page.evaluate("""() => {
      const c = document.querySelector('.login-card').getBoundingClientRect();
      const p = document.querySelector('.form-panel').getBoundingClientRect();
      const b = document.querySelector('.mobile-brand');
      return {iw: innerWidth, sw: document.documentElement.scrollWidth,
              body: document.body.scrollWidth, left: Math.round(c.left),
              right: Math.round(innerWidth - c.right), panelW: Math.round(p.width),
              topbar: !!document.querySelector('.topbar'),
              brand: !!b && getComputedStyle(b).display !== 'none'};
    }""")
    check("登录页无横向溢出", g["sw"] <= g["iw"] + 1 and g["body"] <= g["iw"] + 1, str(g))
    check("登录卡片左右边距对称", abs(g["left"] - g["right"]) <= 1,
          f"left={g['left']} right={g['right']}")
    check("登录页不渲染全局顶栏", not g["topbar"])
    check("登录页显示移动端品牌头", g["brand"])
    shoot(page, "验证-移动端-登录页.png")
    ctx.close()


def run_browse_mode(browser, user: str, pw: str) -> None:
    """空检索词应列出全部档案；无关查询不应再无差别返回全库。"""
    print("\n=== 检索页浏览模式与语义阈值 ===")
    ctx = browser.new_context(viewport={"width": 375, "height": 812},
                              device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    sign_in(page, user, pw)
    page.goto(f"{BASE}/#/", wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelectorAll('.hit').length > 0 || !!document.querySelector('.empty-box')",
        timeout=20000)
    check("检索页首次加载列出全部档案（不再显示暂无结果）",
          page.locator(".hit").count() >= 1, f"实际 {page.locator('.hit').count()} 条")
    check("浏览模式引导语可见",
          page.locator("text=输入检索词开始全文检索").count() > 0)
    check("未识别到文字的档案被明确标注", page.locator(".ocr-empty").count() >= 1)
    ok, detail = no_overflow(page)
    check("检索页无横向溢出", ok, detail)
    shoot(page, "验证-移动端-检索页浏览模式.png")

    page.fill('input[placeholder*="检索词"]', "今天天气怎么样")
    page.locator("button.primary").first.click()
    page.wait_for_timeout(1500)
    check("无关查询不再返回全库", page.locator(".hit").count() == 0,
          f"实际 {page.locator('.hit').count()} 条")
    ctx.close()


def run_low_quality(browser, user: str, pw: str, img: Path) -> None:
    """翻拍屏 / 文字过淡图片必须在归档前给出 OCR 风险提示。"""
    print("\n=== 采集页低对比度图提示 ===")
    ctx = browser.new_context(viewport={"width": 375, "height": 812},
                              device_scale_factor=2, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    sign_in(page, user, pw)
    page.goto(f"{BASE}/#/capture", wait_until="networkidle")
    page.locator("input[type=file]").nth(1).set_input_files(str(img))
    page.wait_for_function(
        "() => document.querySelectorAll('.tag.ok, .tag.err, .tag.warn').length > 0",
        timeout=45000)
    check("低对比度图给出 OCR 风险提示", page.locator(".quality-warn").count() > 0)
    shoot(page, "验证-移动端-采集页质量提示.png")
    ctx.close()


def run_preview_and_paging(browser, user: str, pw: str, label: str,
                           viewport: dict, mobile: bool, full: bool = False) -> None:
    """在线预览（无 iframe / 图片画廊 / 独立预览页）与列表分页（无 50 条上限）。"""
    import re

    print(f"\n=== {label}：在线预览与列表分页 ===")
    ctx = browser.new_context(viewport=viewport,
                              device_scale_factor=2 if mobile else 1,
                              is_mobile=mobile, has_touch=mobile)
    page = ctx.new_page()
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))
    sign_in(page, user, pw)

    # --- 检索页：自定义每页条数 + 真实总数 + 分页器 ---
    page.goto(f"{BASE}/#/", wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelectorAll('.hit').length > 0 || !!document.querySelector('.empty-box')",
        timeout=20000)
    check(f"{label}：检索页不再使用原生条数下拉", page.locator("select").count() == 0)
    check(f"{label}：自定义每页条数控件可用", page.locator(".seg button").count() >= 4)
    bar = page.locator(".result-bar").inner_text() if page.locator(".result-bar").count() else ""
    check(f"{label}：显示真实档案总数与页码", "份档案" in bar and "第 1/" in bar, bar[:80])

    # 切到最小每页条数，逼出分页器并验证能翻到下一页
    page.locator(".seg button").first.click()
    page.wait_for_timeout(1300)
    pager = page.locator(".pager").count() > 0
    check(f"{label}：缩小每页条数后出现分页器", pager,
          "档案总数不足以分页" if not pager else "")
    if pager and full:
        shoot(page, "验证-移动端-检索页分页器.png")
    if pager:
        first = page.locator(".hit").first.inner_text()
        page.locator(".pager .pg-btn").last.click()
        page.wait_for_timeout(1300)
        bar2 = page.locator(".result-bar").inner_text()
        check(f"{label}：分页器可翻到第 2 页", "第 2/" in bar2, bar2[:80])
        check(f"{label}：翻页后列表内容变化",
              page.locator(".hit").first.inner_text() != first)
    ok, detail = no_overflow(page)
    check(f"{label}：检索页分页布局无横向溢出", ok, detail)

    # --- 详情页：图片预览，而不是 iframe 嵌 PDF ---
    page.goto(f"{BASE}/#/", wait_until="networkidle")
    page.wait_for_function("() => document.querySelectorAll('.hit').length > 0", timeout=20000)
    page.locator(".hit").first.locator("a", has_text="查看详情").click()
    page.wait_for_timeout(800)
    page.wait_for_function(
        "() => !!document.querySelector('.pv-img') || !!document.querySelector('.pv-error')",
        timeout=25000)
    check(f"{label}：详情页不再用 iframe 嵌 PDF", page.locator("iframe").count() == 0)
    check(f"{label}：原件渲染为图片预览", page.locator(".pv-img").count() > 0)
    loaded = page.evaluate(
        "() => { const i = document.querySelector('.pv-img'); "
        "return !!(i && i.complete && i.naturalWidth > 100); }")
    check(f"{label}：预览图真实加载成功（非破图）", loaded)
    ok, detail = no_overflow(page)
    check(f"{label}：详情页无横向溢出", ok, detail)

    if full:
        check("预览工具条含原件/归档切换与下载",
              page.locator(".pv-seg button").count() == 2
              and page.locator(".pv-actions .pv-btn").count() >= 2)
        page.locator(".pv-seg button").nth(1).click()
        page.wait_for_timeout(1300)
        check("切换「拍摄原件」后仍可预览", page.locator(".pv-img").count() > 0)
        page.locator(".pv-img").first.click()
        check("放大层可打开", page.locator(".pv-overlay").count() > 0)
        page.locator(".pv-overlay-bar .pv-btn").last.click()
        check("放大层可关闭", page.locator(".pv-overlay").count() == 0)
        shoot(page, "验证-移动端-详情页在线预览.png")

        m = re.search(r"#/doc/(\d+)", page.url)
        did = m.group(1) if m else "1"
        page.goto(f"{BASE}/#/preview/{did}", wait_until="networkidle")
        page.wait_for_function("() => !!document.querySelector('.pv-img')", timeout=25000)
        check("独立预览页无全局顶栏", page.locator(".topbar").count() == 0)
        check("独立预览页渲染图片预览", page.locator(".pv-img").count() > 0)
        shoot(page, "验证-移动端-独立预览页.png")

    check(f"{label}：无 JS 运行时错误", not js_errors, "; ".join(js_errors[:2]))
    ctx.close()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    user, pw = credentials()
    img = make_image()
    low = make_low_contrast_image()
    print(f"档枢 ArcHub · 浏览器回归  BASE={BASE}  OUT={OUT_DIR}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            run_login_layout(browser)
            run_browse_mode(browser, user, pw)
            run_preview_and_paging(browser, user, pw, "桌面",
                                   {"width": 1365, "height": 900}, False)
            run_preview_and_paging(browser, user, pw, "移动端",
                                   {"width": 375, "height": 812}, True, full=True)
            run_low_quality(browser, user, pw, low)
            run_view(browser, "桌面", {"width": 1365, "height": 900}, False, user, pw, img)
            run_view(browser, "移动端", {"width": 375, "height": 812}, True, user, pw, img)
            run_auth_recovery(browser, user, pw, img)
        finally:
            browser.close()
    print(f"\n=== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ===")
    if FAIL:
        print("失败项：", *FAIL, sep="\n  - ")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
