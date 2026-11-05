#!/usr/bin/env python3
"""端到端验证 v2.0：断言式，覆盖安全底座 + 检索 + 扫描增强。

用法：
  python3 scripts/e2e_test.py            # 验证已有数据（迁移后）
  python3 scripts/e2e_test.py --fresh    # 重新上传 8 份样本并全链路验证
退出码非 0 即有断言失败。
"""
import json
import os
import sys
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

GATEWAY = os.getenv("GATEWAY", "http://127.0.0.1:8700")
T = httpx.Timeout(120.0)
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not cond else ""))


def sep(t):
    print("\n" + "=" * 66 + f"\n  {t}\n" + "=" * 66)


def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
CREDS = os.path.join(ROOT, "data", "admin_credentials.txt")
ADMIN_USER, ADMIN_PW = "admin", ""
for line in open(CREDS, encoding="utf-8"):
    if ":" in line:
        ADMIN_USER, ADMIN_PW = line.split(":", 1)
        ADMIN_PW = ADMIN_PW.strip()
        break


def login(user, pw):
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   data={"username": user, "password": pw}, timeout=T)
    return r


def fresh_upload():
    from make_samples import ARCHIVES, OUT_DIR
    with httpx.Client(timeout=httpx.Timeout(300.0)) as c:
        toks = c.post(f"{GATEWAY}/api/v1/auth/login",
                      data={"username": ADMIN_USER, "password": ADMIN_PW}, timeout=T).json()
        h = {"Authorization": f"Bearer {toks['access_token']}"}
        for a in ARCHIVES:
            with open(os.path.join(OUT_DIR, a["file"]), "rb") as f:
                r = c.post(f"{GATEWAY}/api/v1/upload", headers=h,
                           files={"file": (a["file"], f, "application/pdf")},
                           data={"title": a["title"], "created": a["created"],
                                 "asn": str(a["asn"]), "correspondent": a["correspondent"],
                                 "document_type": a["doc_type"], "tags": ",".join(a["tags"])})
            print(f"  upload DA-{a['asn']} {a['title']} -> {r.status_code}")
        # 等 OCR：直接查 Paperless 文档数（走网关代理之外，用管理统计即可）
        start = time.time()
        while time.time() - start < 600:
            st = c.get(f"{GATEWAY}/api/v1/stats", headers=h, timeout=T).json()
            total = st.get("total_documents", 0)
            if total >= len(ARCHIVES):
                print(f"  已入库 {total} 份（{int(time.time()-start)}s）")
                break
            time.sleep(5)
        else:
            print("  !! OCR 入库超时")


def wait_semantic(expect_docs=8, limit=600):
    """等 indexer 把 OCR 文本编码进常驻矩阵。"""
    start = time.time()
    with httpx.Client(timeout=T) as c:
        while time.time() - start < limit:
            st = c.get(f"{GATEWAY}/api/v1/stats", timeout=T).json()
            docs = st.get("semantic_index", {}).get("documents", 0)
            pending = st.get("index_queue", {}).get("pending", 0)
            if docs >= expect_docs and pending == 0:
                print(f"  语义索引就绪：{docs} 份（{int(time.time()-start)}s）")
                return True
            time.sleep(5)
    print("  !! 语义索引未追平")
    return False


def main():
    fresh = "--fresh" in sys.argv
    sep("档案检索栈 v2.0 · 端到端断言验证")
    c = httpx.Client(timeout=T)

    # ---- 0) 探针 ----
    r = c.get(f"{GATEWAY}/health")
    check("health 200", r.status_code == 200)
    r = c.get(f"{GATEWAY}/ready")
    check("ready 200（全依赖健康）", r.status_code == 200, r.text[:120])

    # ---- 1) 鉴权 ----
    r = c.get(f"{GATEWAY}/api/v1/search", params={"mode": "keyword", "q": "消防"})
    check("未带令牌访问 401", r.status_code == 401)
    r = login(ADMIN_USER, "wrong-password")
    check("错误密码 401", r.status_code == 401)
    r = login(ADMIN_USER, ADMIN_PW)
    check("管理员登录拿 JWT", r.status_code == 200 and "access_token" in r.json())
    admin_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # ---- 2) 数据准备 ----
    r = c.get(f"{GATEWAY}/api/v1/stats", headers=admin_h)
    total = r.json().get("total_documents") or 0
    if fresh or total < 8:
        print(f"  当前文档 {total} 份，走 fresh 全链路")
        fresh_upload()
    r = c.get(f"{GATEWAY}/api/v1/documents", headers=admin_h)
    docs = r.json().get("results", r.json().get("items", []))
    check(f"文档列表非空（{len(docs)} 份）", len(docs) > 0)
    if docs:
        doc_id = docs[0]["id"]
        r = c.get(f"{GATEWAY}/api/v1/documents/{doc_id}", headers=admin_h)
        check("文档详情可读", r.status_code == 200)
        r = c.get(f"{GATEWAY}/api/v1/documents/{doc_id}/download", headers=admin_h)
        check("下载 200 且 nosniff",
              r.status_code == 200 and r.headers.get("x-content-type-options") == "nosniff")

    # ---- 3) RBAC ----
    r = c.post(f"{GATEWAY}/api/v1/admin/users", headers=admin_h,
               data={"username": "e2e_viewer", "password": "E2e#viewer1",
                     "roles": "viewer", "clearance": "1"})
    check("创建 viewer 用户（幂等：已存在亦视为通过）",
          r.status_code in (200, 409), r.text[:120])
    # 已存在用户可能残留旧密码/被禁用，统一按当前用例重置，保证后续断言可控
    if r.status_code == 409:
        users = c.get(f"{GATEWAY}/api/v1/admin/users", headers=admin_h).json().get("users", [])
        uid = next((x["id"] for x in users if x["username"] == "e2e_viewer"), None)
        if uid:
            c.patch(f"{GATEWAY}/api/v1/admin/users/{uid}", headers=admin_h,
                    data={"password": "E2e#viewer1", "status": "active", "clearance": "1"})
    r = login("e2e_viewer", "E2e#viewer1")
    check("viewer 登录", r.status_code == 200)
    viewer_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = c.get(f"{GATEWAY}/api/v1/admin/users", headers=viewer_h)
    check("viewer 访问管理端点 403", r.status_code == 403)
    r = c.get(f"{GATEWAY}/api/v1/admin/audit-logs", headers=viewer_h)
    check("viewer 访问审计 403", r.status_code == 403)

    # ---- 4) 密级 fail-closed ----
    if docs:
        doc_id = docs[0]["id"]
        # 先记录原始密级与归属部门。旧实现验证结束硬编码复位为 0，会把被抽检
        # 档案的密级永久改成「公开」，属于测试脚本自身的数据副作用，必须消除。
        sec0 = c.get(f"{GATEWAY}/api/v1/admin/docs/{doc_id}/security",
                     headers=admin_h).json()
        orig_cls = int(sec0.get("classification", 1))
        orig_dept = sec0.get("owner_dept")
        r = c.put(f"{GATEWAY}/api/v1/admin/docs/{doc_id}/security", headers=admin_h,
                  data={"classification": "3"})
        check("设置密级=机密", r.status_code == 200)
        r = c.get(f"{GATEWAY}/api/v1/documents/{doc_id}", headers=viewer_h)
        check("viewer 越权读机密件被拒（fail-closed）", r.status_code == 403)
        r = c.post(f"{GATEWAY}/api/v1/index", headers=viewer_h)
        check("viewer 触发重建 403", r.status_code == 403)
        restore = {"classification": str(orig_cls)}
        if orig_dept:
            restore["owner_dept"] = orig_dept
        c.put(f"{GATEWAY}/api/v1/admin/docs/{doc_id}/security",
              headers=admin_h, data=restore)  # 精确还原原始密级

    # ---- 5) 检索四模式 ----
    if fresh:
        wait_semantic()
    cases = [
        ("元数据精确查询", {"mode": "metadata", "asn": 1006}),
        ("关键词全文检索", {"mode": "keyword", "q": "消防"}),
        ("语义检索（大白话）", {"mode": "semantic", "q": "出差住酒店能报销多少钱"}),
        ("混合检索", {"mode": "hybrid", "q": "灭火器过期了怎么处理"}),
    ]
    for name, params in cases:
        r = c.get(f"{GATEWAY}/api/v1/search", headers=admin_h, params=params)
        ok = r.status_code == 200 and len(r.json().get("results", [])) > 0
        n = len(r.json().get("results", [])) if r.status_code == 200 else -1
        check(f"检索-{name}（{n} 条）", ok)

    # ---- 6) 上传校验 ----
    r = c.post(f"{GATEWAY}/api/v1/upload", headers=admin_h,
               files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    check("非 PDF 内容被拒", r.status_code in (400, 415, 422))
    r = c.post(f"{GATEWAY}/api/v1/upload", headers=viewer_h,
               files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    check("viewer 无上传权限被拒", r.status_code == 403)

    # ---- 7) 扫描增强（CamScanner 对齐）----
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (220, 220, 210)).save(buf, "JPEG")
    r = c.post(f"{GATEWAY}/api/v1/scan/enhance", headers=admin_h,
               files={"file": ("photo.jpg", buf.getvalue(), "image/jpeg")},
               data={"mode": "enhance"})
    check("扫描增强端点 200", r.status_code == 200 and len(r.content) > 1000)

    # ---- 8) 统计与审计 ----
    r = c.get(f"{GATEWAY}/api/v1/admin/audit-logs", headers=admin_h)
    n_logs = r.json().get("total", 0)
    check(f"审计日志有记录（{n_logs} 条）", n_logs > 0)
    r = c.get(f"{GATEWAY}/metrics")
    check("metrics 可拉取", r.status_code == 200)

    sep(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("  失败项：", *FAIL, sep="\n    - ")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
