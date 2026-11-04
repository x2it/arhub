#!/usr/bin/env python3
"""检索接口压测：并发打 /api/v1/search，统计 QPS 与延迟分位。

用法：python3 scripts/load_test.py [并发数] [总请求数]
前提：栈已 running，data/admin_credentials.txt 存在。
"""
import os
import sys
import threading
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = os.getenv("GATEWAY", "http://127.0.0.1:8700")
CONC = int(sys.argv[1]) if len(sys.argv) > 1 else 8
TOTAL = int(sys.argv[2]) if len(sys.argv) > 2 else 200

QUERIES = [
    "出差住宿报销标准", "消防设施检查", "安全生产责任", "公积金缴存比例",
    "会议纪要 2023", "灭火器过期处理", "采购流程审批", "员工培训计划",
]


def admin_token():
    user, pw = "admin", ""
    for line in open(os.path.join(ROOT, "data", "admin_credentials.txt"), encoding="utf-8"):
        if ":" in line:
            user, pw = line.split(":", 1)
            pw = pw.strip()
            break
    r = httpx.post(f"{GATEWAY}/api/v1/auth/login",
                   data={"username": user, "password": pw}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    token = admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    lat, errs = [], []
    lock = threading.Lock()
    counter = {"i": 0}
    barrier = threading.Barrier(CONC)

    def worker(n):
        with httpx.Client(timeout=httpx.Timeout(60.0), headers=headers) as c:
            barrier.wait()
            for _ in range(n):
                with lock:
                    idx = counter["i"]
                    counter["i"] += 1
                q = QUERIES[idx % len(QUERIES)]
                mode = ("hybrid", "semantic", "keyword", "metadata")[idx % 4]
                params = {"mode": mode, "q": q} if mode != "metadata" else {"mode": mode, "asn": 1006}
                t0 = time.perf_counter()
                try:
                    r = c.get(f"{GATEWAY}/api/v1/search", params=params)
                    dt = (time.perf_counter() - t0) * 1000
                    with lock:
                        (lat if r.status_code == 200 else errs).append(
                            dt if r.status_code == 200 else f"{r.status_code}")
                except Exception as e:  # noqa: BLE001
                    with lock:
                        errs.append(type(e).__name__)

    per = TOTAL // CONC
    threads = [threading.Thread(target=worker, args=(per,)) for _ in range(CONC)]
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t_start

    lat.sort()
    def pct(p):
        return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0

    print(f"并发 {CONC} · 总请求 {len(lat) + len(errs)} · 耗时 {elapsed:.2f}s")
    print(f"QPS          : {len(lat) / elapsed:.1f}")
    print(f"P50 / P95/P99: {pct(0.5):.0f}ms / {pct(0.95):.0f}ms / {pct(0.99):.0f}ms")
    print(f"失败         : {len(errs)}" + (f"  样本 {errs[:5]}" if errs else ""))
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
