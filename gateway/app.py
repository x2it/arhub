"""统一网关 v2.0：唯一对外入口。

相对 v1 原型的系统性升级：
- 零鉴权 → JWT + RBAC + 密级越权过滤（fail-closed）+ 审计 + 请求ID
- CORS * → 白名单；无限流 → 令牌桶；错误裸奔 → 统一结构化错误
- hybrid 串行 → gather 并发 + 熔断降级；每请求新 client → 共享连接池
- 上传全量读内存无校验 → 大小/类型校验 + 幂等
- /health 永远 200 → liveness 200 + /ready 503
- 手动全量索引 → /internal/index 回调 + indexer outbox
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import secrets
import time
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from gateway import audit as audit_mod
from gateway.clients import CircuitOpen, UpstreamError, Upstreams
from gateway.deps import (
    UserCtx,
    admin_required,
    can_view,
    current_user,
    load_doc_security,
    post_filter_hits,
    require_view_one,
)
from gateway.errors import install_error_handlers
from gateway.middleware import RateLimitMiddleware, RequestContextMiddleware, bind_actor
from gateway.schemas import LoginResponse, SearchHit, SearchResponse, TaskAccepted
from gateway.security import (
    ROLE_ADMIN,
    ROLE_ARCHIVIST,
    ROLE_AUDITOR,
    ROLE_VIEWER,
    ALL_ROLES,
    create_access_token,
    has_role,
    hash_password,
    verify_password,
)
from shared.db import get_pg
from shared.logging_conf import request_id_var, setup_logging
from shared.settings import get_settings

log = logging.getLogger("gateway")
VERSION = "2.0.0"

REQ_COUNT = Counter("gw_requests_total", "requests", ["method", "path", "status"])
SEARCH_LAT = Histogram("gw_search_latency_seconds", "search latency", ["mode"])

up: Upstreams  # lifespan 初始化


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global up
    setup_logging()
    up = Upstreams()
    await _bootstrap()
    yield
    await up.aclose()


def s_paperless_admin() -> str:
    return os.getenv("PAPERLESS_ADMIN_USER", "admin")


async def _bootstrap() -> None:
    """启动期治理：种子 admin、换 Paperless Token。"""
    cred_dir = os.getenv("DATA_DIR", "/srv/data")
    cred_file = os.path.join(cred_dir, "admin_credentials.txt")
    # 1) 种子 admin（密码随机生成，落盘 600 权限文件，绝无默认口令）
    with get_pg() as conn:
        row = conn.execute("SELECT id FROM archive.users WHERE username=%s",
                           (s_paperless_admin(),)).fetchone()
        need_cred = row is None
        if row is None:
            admin_pw = secrets.token_urlsafe(12)
            conn.execute(
                "INSERT INTO archive.users(username,pwd_hash,display_name,clearance_level,status)"
                " VALUES (%s,%s,%s,3,'active')",
                (s_paperless_admin(), hash_password(admin_pw), "系统管理员"))
            conn.execute(
                "INSERT INTO archive.user_roles(user_id,role_id)"
                " SELECT u.id,r.id FROM archive.users u, archive.roles r"
                " WHERE u.username=%s AND r.name=%s ON CONFLICT DO NOTHING",
                (s_paperless_admin(), ROLE_ADMIN))
            log.warning("已创建种子管理员，初始密码见 %s", cred_file)
        elif not os.path.exists(cred_file):
            # 自愈：用户存在但凭据文件丢失（首启时目录不可写）→ 重置密码并重新落盘
            admin_pw = secrets.token_urlsafe(12)
            conn.execute("UPDATE archive.users SET pwd_hash=%s WHERE id=%s",
                         (hash_password(admin_pw), row[0]))
            need_cred = True
            log.warning("凭据文件缺失，已重置管理员密码")
        else:
            admin_pw = ""
        if need_cred:
            try:
                os.makedirs(cred_dir, exist_ok=True)
                with open(cred_file, "w") as f:
                    os.chmod(cred_file, 0o600)
                    f.write(f"{s_paperless_admin()}: {admin_pw}\n")
            except OSError as e:
                log.error("凭据文件不可写（%s），请检查 DATA_DIR=%s 挂载权限", e, cred_dir)
    # 2) 换 Paperless Token：env → 库内既有 token → 管理员凭据换取（带重试）
    token = os.getenv("PAPERLESS_TOKEN", "")
    if not token:
        # 容器休眠恢复时网关常先于 Paperless 启动，换取会失败；先取库内上次落库值
        with contextlib.suppress(Exception):
            with get_pg() as conn:
                row = conn.execute(
                    "SELECT value FROM archive.sys_config WHERE key='paperless_token'"
                ).fetchone()
            token = row[0] if row else ""
        if token:
            log.info("复用 sys_config 中已落库的 Paperless Token")
    if not token:
        for attempt in range(1, 6):
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.post(f"{get_settings().paperless_url}/api/token/",
                                     json={"username": s_paperless_admin(),
                                           "password": os.getenv("PAPERLESS_ADMIN_PASSWORD", "")})
                if r.status_code == 200:
                    token = r.json().get("token", "")
                    if token:
                        break
                log.warning("换取 Paperless Token 未成功（第 %s 次，HTTP %s）",
                            attempt, r.status_code)
            except Exception as e:
                log.warning("换取 Paperless Token 失败（第 %s 次）: %s", attempt, e)
            await asyncio.sleep(min(2 * attempt, 8))
    if token:
        up.set_pl_token(token)
        # 落库共享：indexer 无凭据、拿不到 token，靠此表取用
        try:
            with get_pg() as conn:
                conn.execute(
                    "INSERT INTO archive.sys_config(key,value) VALUES ('paperless_token',%s)"
                    " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                    (token,))
        except Exception as e:
            log.error("Paperless Token 落库失败: %s", e)
        log.info("Paperless Token 就绪")
    else:
        log.error("Paperless Token 暂不可用；pl_headers 将在请求时再次尝试从库恢复")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


app = FastAPI(
    title="档枢 ArcHub · 智能档案中枢 API",
    version=VERSION,
    description=(
        "扫描归档 → OCR 著录 → 密级管控 → 全文/语义检索 → 全量审计，一体化私有化档案平台。"
        "四种检索模式：metadata / keyword / semantic / hybrid；所有访问均需 JWT 并受密级裁决。"
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

s = get_settings()
if s.cors_origin_list:
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["*"])
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RateLimitMiddleware, per_min=s.rate_limit_per_min)
install_error_handlers(app)


# ================= 健康与可观测 =================
@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION}


@app.get("/ready")
async def ready():
    """readiness：依赖探测，失败 503（告警按状态码而非 200 body）。"""
    checks: dict[str, str] = {}
    try:
        await up.pl_get("/api/documents/", {"page_size": 1})
        checks["paperless"] = "ok"
    except Exception as e:
        checks["paperless"] = f"error: {type(e).__name__}"
    try:
        r = await up.sem.get("/health", headers=up.internal_headers(), timeout=5)
        checks["semantic"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception as e:
        checks["semantic"] = f"error: {type(e).__name__}"
    try:
        with get_pg() as conn:
            conn.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {type(e).__name__}"
    if not all(v == "ok" for v in checks.values()):
        return JSONResponse(status_code=503,
                            content={"status": "degraded", "checks": checks,
                                     "request_id": request_id_var.get()})
    return {"status": "ok", "checks": checks}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ================= 认证 =================
@app.post("/api/v1/auth/login", response_model=LoginResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    t0 = time.perf_counter()
    rid = request_id_var.get()
    with get_pg() as conn:
        row = conn.execute(
            "SELECT u.id,u.username,u.pwd_hash,u.clearance_level,u.status,"
            "COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL),'{}') "
            "FROM archive.users u LEFT JOIN archive.user_roles ur ON ur.user_id=u.id "
            "LEFT JOIN archive.roles r ON r.id=ur.role_id "
            "WHERE u.username=%s GROUP BY u.id", (username,)).fetchone()
    ok = bool(row) and row[4] == "active" and verify_password(password, row[2])
    if not ok:
        await audit_mod.write_audit(
            request_id=rid, actor_id=row[0] if row else None, actor_name=username,
            actor_roles=[], action="login", decision="deny", status_code=401,
            latency_ms=int((time.perf_counter() - t0) * 1000), ip=_client_ip(request),
            tenant_id=s.tenant_id)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    uid, uname, _, clearance, _, roles = row
    token, expires_in = create_access_token(uid, uname, roles, clearance)
    bind_actor(uname)
    await audit_mod.write_audit(
        request_id=rid, actor_id=uid, actor_name=uname, actor_roles=roles, action="login",
        decision="allow", status_code=200, latency_ms=int((time.perf_counter() - t0) * 1000),
        ip=_client_ip(request), tenant_id=s.tenant_id)
    return LoginResponse(access_token=token, expires_in=expires_in,
                         user={"id": uid, "username": uname, "roles": roles,
                               "clearance": clearance})


# ================= 字典解析（Redis 缓存 + 分页遍历，失败不静默）=================
async def ensure_id(kind: str, name: str) -> Optional[int]:
    if not name:
        return None
    cached = None
    try:
        import redis.asyncio as aredis

        r = aredis.from_url(s.redis_url, decode_responses=True, socket_timeout=2)
        cached = await r.get(f"idcache:{kind}:{name}")
        if cached:
            return int(cached)
    except Exception:
        r = None  # Redis 不可用时退化为直查 Paperless
    items = await up.pl_get(f"/api/{kind}/", {"page_size": 1000})
    found = None
    while True:
        for item in items.get("results", []):
            if item.get("name") == name:
                found = item["id"]
                break
        if found or not items.get("next"):
            break
        next_url = items["next"]
        path = next_url.split(f"/api/{kind}/")[-1]
        items = await up.pl_get(f"/api/{kind}/", dict(p.split("=") for p in path.split("&") if "=" in p))
    if found is None:
        resp = await up.pl.post(f"/api/{kind}/", headers=up.pl_headers(), json={"name": name})
        if resp.status_code in (200, 201):
            found = resp.json()["id"]
    if found is None:
        log.error("字典解析失败 kind=%s name=%s（fail-closed：调用方应拒绝）", kind, name)
        return None
    if r is not None:
        with contextlib.suppress(Exception):
            await r.set(f"idcache:{kind}:{name}", str(found), ex=3600)
    return found


# ================= 检索 =================
def _snippet(text: str, q: str, width: int = 160) -> str:
    """在原文中截取命中位置前后文，供前端高亮。"""
    t = re.sub(r"\s+", " ", text or "")
    pos = t.lower().find(q.lower()) if q else -1
    if pos < 0:
        return t[:width]
    start = max(0, pos - width // 3)
    frag = t[start:start + width]
    return ("…" if start > 0 else "") + frag + ("…" if start + width < len(t) else "")


async def search_keyword(q: str, top_k: int) -> list[dict]:
    """关键词检索：走 PostgreSQL trigram，不用 Paperless 的 Tantivy。

    Paperless 全文索引使用 Tantivy，默认分词器不切分中文，`query=消防` 恒为 0 命中。
    改为直接对 OCR 全文做 ILIKE，配合 gin_trgm_ops 索引，中文子串匹配准确且可用。
    """
    q = (q or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    with get_pg() as conn:
        rows = conn.execute(
            "SELECT id, title, archive_serial_number, created, content,"
            "       similarity(title, %s) AS sim"
            " FROM archive.documents_document"
            " WHERE title ILIKE %s OR content ILIKE %s"
            " ORDER BY (title ILIKE %s) DESC, sim DESC, created DESC"
            " LIMIT %s",
            (q, like, like, like, top_k)).fetchall()
    return [{"doc_id": r[0], "title": r[1], "asn": r[2],
             "created": str(r[3])[:10] if r[3] else None,
             "score": round(float(r[5] or 0), 4),
             "snippet": _snippet(r[4] or "", q),
             "source": "keyword"} for r in rows]


async def search_semantic(q: str, top_k: int) -> list[dict]:
    data = await up.sem_request("GET", "/semantic/search", params={"q": q, "top_k": top_k})
    return [{"doc_id": h["doc_id"], "title": h.get("doc_title"), "asn": h.get("asn"),
             "created": h.get("created"), "score": h.get("score"),
             "chunk_index": h.get("chunk_index"),
             "snippet": (h.get("text") or "")[:220], "source": "semantic"} for h in data]


def rrf_fuse(*lists: list[dict], top_k: int) -> list[dict]:
    fused: dict[int, dict] = {}
    for results in lists:
        for rank, item in enumerate(results):
            rec = fused.setdefault(item["doc_id"], {
                **{k: item.get(k) for k in ("doc_id", "title", "asn", "created")},
                "rrf_score": 0.0, "matched_by": [], "snippet": item.get("snippet"),
                "highlights": item.get("highlights"),
                "chunk_index": item.get("chunk_index")})
            rec["rrf_score"] += 1.0 / (s.rrf_k + rank + 1)
            src = item.get("source")
            if src and src not in rec["matched_by"]:
                rec["matched_by"].append(src)
            for k in ("title", "asn", "created", "snippet", "highlights",
                      "chunk_index", "score"):
                if rec.get(k) in (None, "") and item.get(k) is not None:
                    rec[k] = item[k]
    ranked = sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)
    for rec in ranked:
        rec["rrf_score"] = round(rec["rrf_score"], 6)
    return ranked[:top_k]


async def search_metadata(asn, created_after, created_before, correspondent,
                          document_type, tag, top_k) -> list[dict]:
    params: dict[str, Any] = {"page_size": top_k}
    if asn is not None:
        params["archive_serial_number"] = asn
    if created_after:
        params["created__date__gt"] = created_after
    if created_before:
        params["created__date__lt"] = created_before
    if correspondent:
        params["correspondent__name__icontains"] = correspondent
    if document_type:
        params["document_type__name__icontains"] = document_type
    if tag:
        tid = await ensure_id("tags", tag)
        if tid is None:
            # fail-closed：过滤条件解析失败 → 空结果，绝不退化为全库
            return []
        params["tags__id__in"] = str(tid)
    data = await up.pl_get("/api/documents/", params)
    return [{"doc_id": d["id"], "title": d.get("title"),
             "asn": d.get("archive_serial_number"), "created": d.get("created"),
             "source": "metadata"} for d in data.get("results", [])]


@app.get("/api/v1/search", response_model=SearchResponse)
async def search(
    request: Request,
    q: Optional[str] = Query(None, max_length=200),
    mode: str = Query("hybrid", pattern="^(metadata|keyword|semantic|hybrid)$"),
    asn: Optional[int] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    correspondent: Optional[str] = None,
    document_type: Optional[str] = None,
    tag: Optional[str] = None,
    top_k: int = Query(s.default_top_k, ge=1, le=s.max_top_k),
    u: UserCtx = Depends(current_user),
):
    t0 = time.perf_counter()
    rid = request_id_var.get()
    degraded = False
    try:
        if mode == "metadata":
            raw = await search_metadata(asn, created_after, created_before,
                                        correspondent, document_type, tag, top_k)
        elif mode == "keyword":
            raw = await search_keyword(q or "", top_k)
        elif mode == "semantic":
            raw = await search_semantic(q or "", top_k)
        else:
            kw_r, sem_r = await asyncio.gather(
                search_keyword(q or "", top_k),
                search_semantic(q or "", top_k),
                return_exceptions=True)
            kw = kw_r if not isinstance(kw_r, BaseException) else []
            sem = sem_r if not isinstance(sem_r, BaseException) else []
            if isinstance(sem_r, BaseException):
                degraded = True  # 语义路故障/熔断 → 仅关键词，明确标记
                log.warning("hybrid 降级：语义路不可用 (%r)", sem_r)
            raw = rrf_fuse(kw, sem, top_k=top_k)
        filtered = post_filter_hits(u, raw)  # 统一越权过滤（覆盖语义路）
        # hybrid 下 matched_by 才是真实来源，source 标为 hybrid；单路则标该路名
        results = [SearchHit(**{**{k: v for k, v in h.items() if k in SearchHit.model_fields},
                               "source": ("hybrid" if mode == "hybrid" else mode)})
                   for h in filtered]
        SEARCH_LAT.labels(mode).observe(time.perf_counter() - t0)
        await audit_mod.write_audit(
            request_id=rid, actor_id=u.id, actor_name=u.username, actor_roles=u.roles,
            action="search", resource_type="search", resource_id=mode, decision="allow",
            status_code=200, latency_ms=int((time.perf_counter() - t0) * 1000),
            ip=_client_ip(request),
            detail={"q": q, "asn": asn, "hits": len(results)}, tenant_id=s.tenant_id)
        return SearchResponse(request_id=rid, mode=mode, total=len(results),
                              degraded=degraded, results=results)
    except (UpstreamError, CircuitOpen) as e:
        status = getattr(e, "status", 503)
        await audit_mod.write_audit(
            request_id=rid, actor_id=u.id, actor_name=u.username, actor_roles=u.roles,
            action="search", resource_type="search", resource_id=mode, decision="deny",
            status_code=status, latency_ms=int((time.perf_counter() - t0) * 1000),
            ip=_client_ip(request), tenant_id=s.tenant_id)
        raise HTTPException(status_code=status, detail=str(e))


# ================= 档案详情 / 下载（先裁决再回源）=================
async def _deny_audit(request: Request, u: UserCtx, action: str, doc_id: int,
                      status: int, t0: float) -> None:
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action=action, resource_type="document",
        resource_id=str(doc_id), decision="deny", status_code=status,
        latency_ms=int((time.perf_counter() - t0) * 1000), ip=_client_ip(request),
        tenant_id=s.tenant_id)


@app.get("/api/v1/documents")
async def list_documents(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                         q: str = "", u: UserCtx = Depends(current_user)):
    """档案列表：回源 Paperless 分页，再按密级过滤（无记录即剔除，fail-closed）。"""
    try:
        data = await up.pl_get("/api/documents/", {
            "page": page, "page_size": page_size, **({"query": q} if q else {})})
    except UpstreamError as e:
        raise HTTPException(status_code=getattr(e, "status", 503),
                            detail="档案服务暂不可用") from e
    raw = data.get("results", [])
    sec = load_doc_security([d["id"] for d in raw])
    results = []
    for d in raw:
        s = sec.get(d["id"])
        if not s or not can_view(u, s["classification"], s.get("owner_dept")):
            continue
        results.append({
            "id": d["id"], "title": d.get("title"),
            "asn": d.get("archive_serial_number"),
            "created": (d.get("created") or "")[:10],
            "added": (d.get("added") or "")[:10],
            "correspondent": d.get("correspondent"),
            "document_type": d.get("document_type"),
            "tags": d.get("tags") or [],
            "classification": s["classification"],
            "owner_dept": s.get("owner_dept"),
            "has_content": bool((d.get("content") or "").strip()),
        })
    return {"total": data.get("count", 0), "page": page, "page_size": page_size,
            "visible": len(results), "results": results,
            "request_id": request_id_var.get()}


@app.get("/api/v1/documents/{doc_id}")
async def get_document(doc_id: int, request: Request, content: bool = True,
                       u: UserCtx = Depends(current_user)):
    rid = request_id_var.get()
    t0 = time.perf_counter()
    try:
        sec = await require_view_one(u, doc_id)  # fail-closed：无密级记录=404
    except HTTPException as e:
        await _deny_audit(request, u, "view", doc_id, e.status_code, t0)
        raise
    data = await up.pl_get(f"/api/documents/{doc_id}/")
    if not content:
        data.pop("content", None)
    data["classification"] = sec["classification"]
    await audit_mod.write_audit(
        request_id=rid, actor_id=u.id, actor_name=u.username, actor_roles=u.roles,
        action="view", resource_type="document", resource_id=str(doc_id),
        classification=sec["classification"], decision="allow", status_code=200,
        latency_ms=int((time.perf_counter() - t0) * 1000), ip=_client_ip(request),
        tenant_id=s.tenant_id)
    return data


@app.get("/api/v1/documents/{doc_id}/download")
async def download_document(doc_id: int, request: Request, original: bool = False,
                            inline: bool = False, u: UserCtx = Depends(current_user)):
    t0 = time.perf_counter()
    try:
        sec = await require_view_one(u, doc_id)
    except HTTPException as e:
        await _deny_audit(request, u, "download", doc_id, e.status_code, t0)
        raise
    try:
        r = await up.pl.get(f"/api/documents/{doc_id}/download/",
                            headers=up.pl_headers(),
                            params={"original": "true" if original else "false"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail="获取档案原件失败") from e
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="download", resource_type="document",
        resource_id=str(doc_id), classification=sec["classification"],
        decision="allow", status_code=200,
        latency_ms=int((time.perf_counter() - t0) * 1000), ip=_client_ip(request),
        tenant_id=s.tenant_id)
    return StreamingResponse(
        iter([r.content]), media_type="application/pdf",
        headers={"Content-Disposition": "inline" if inline else "attachment",
                 "X-Content-Type-Options": "nosniff"})


# ================= 上传（校验 + 幂等）=================
PDF_MAGIC = b"%PDF"
JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG"


@app.post("/api/v1/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    created: Optional[str] = Form(None),
    asn: Optional[int] = Form(None),
    correspondent: Optional[str] = Form(None),
    document_type: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    classification: int = Form(1, ge=0, le=3),
    idem_key: Optional[str] = Form(None),
    u: UserCtx = Depends(current_user),
):
    rid = request_id_var.get()
    t0 = time.perf_counter()
    if not has_role(u.roles, ROLE_ARCHIVIST, ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="需要档案员或管理员权限")
    # 流式读取 + 大小/类型校验（修复 file.read() 全量入内存）
    limit = s.max_upload_mb * 1024 * 1024
    head = await file.read(5)
    # 扫描采集产生的是图片（JPG/PNG），Paperless 原生支持图片 OCR 入库，
    # 因此网关需放行 PDF / JPEG / PNG 三类，并按魔数判定真实类型。
    if head.startswith(PDF_MAGIC):
        upload_ctype = "application/pdf"
    elif head.startswith(JPEG_MAGIC):
        upload_ctype = "image/jpeg"
    elif head.startswith(PNG_MAGIC):
        upload_ctype = "image/png"
    else:
        raise HTTPException(status_code=400, detail="仅支持 PDF / JPG / PNG 文件")
    chunks, total = [head], 5
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413,
                                detail=f"文件超过 {s.max_upload_mb}MB 上限")
        chunks.append(chunk)
    payload = b"".join(chunks)
    content_sha = hashlib.sha256(payload).hexdigest()
    key = idem_key or content_sha
    # 暂存「用户选定的密级 → 原始文件 sha256」。Paperless 的 post_document 返回任务
    # UUID 而非数字 doc_id，此刻还不知道密级该挂到哪个文档上；等 post-consume 回调带回
    # 真实 doc_id 后，用 Paperless 的 checksum（即本文件 sha256）回查并落定。
    with get_pg() as conn:
        conn.execute(
            "INSERT INTO archive.pending_classification(content_sha,classification)"
            " VALUES (%s,%s) ON CONFLICT (content_sha)"
            " DO UPDATE SET classification=EXCLUDED.classification",
            (content_sha, classification))
    with get_pg() as conn:
        row = conn.execute("SELECT doc_id FROM archive.upload_idem WHERE idem_key=%s",
                           (key,)).fetchone()
        if row and row[0]:
            return {"status": "ok", "doc_id": row[0], "duplicate": True,
                    "message": "重复上传，已返回既有档案", "request_id": rid}
    # 字典解析：失败即拒绝（字段绝不静默丢失）
    data: dict[str, Any] = {}
    if title:
        data["title"] = title
    if created:
        data["created"] = created
    if asn is not None:
        data["archive_serial_number"] = asn
    if correspondent:
        cid = await ensure_id("correspondents", correspondent)
        if cid is None:
            raise HTTPException(status_code=400, detail=f"责任者字典解析失败: {correspondent}")
        data["correspondent"] = cid
    if document_type:
        did = await ensure_id("document_types", document_type)
        if did is None:
            raise HTTPException(status_code=400, detail=f"门类字典解析失败: {document_type}")
        data["document_type"] = did
    if tags:
        tids = []
        for name in (x.strip() for x in tags.split(",")):
            if not name:
                continue
            tid = await ensure_id("tags", name)
            if tid is None:
                raise HTTPException(status_code=400, detail=f"标签字典解析失败: {name}")
            tids.append(tid)
        if tids:
            data["tags"] = tids
    try:
        r = await up.pl.post(
            "/api/documents/post_document/", headers=up.pl_headers(), data=data,
            files={"document": (file.filename, payload, upload_ctype)})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail="档案服务暂不可用，上传未入队") from e
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502,
                            detail=f"档案服务拒绝入库（HTTP {r.status_code}）")
    raw = r.json()
    doc_id = int(raw) if str(raw).isdigit() else None
    if doc_id:
        # 密级登记 + 幂等记录（fail-closed 前提：每份档案必有密级记录）
        with get_pg() as conn:
            conn.execute(
                "INSERT INTO archive.doc_security(doc_id,classification,owner_dept,tenant_id)"
                " VALUES (%s,%s,%s,%s) ON CONFLICT (doc_id) DO NOTHING",
                (doc_id, classification, u.dept or None, s.tenant_id))
            conn.execute(
                "INSERT INTO archive.upload_idem(idem_key,content_sha,doc_id)"
                " VALUES (%s,%s,%s) ON CONFLICT (idem_key) DO UPDATE SET doc_id=EXCLUDED.doc_id",
                (key, content_sha, doc_id))
    await audit_mod.write_audit(
        request_id=rid, actor_id=u.id, actor_name=u.username, actor_roles=u.roles,
        action="upload", resource_type="document", resource_id=str(doc_id or ""),
        classification=classification, decision="allow", status_code=200,
        latency_ms=int((time.perf_counter() - t0) * 1000), ip=_client_ip(request),
        detail={"title": title, "asn": asn, "bytes": total}, tenant_id=s.tenant_id)
    return {"status": "queued", "doc_id": doc_id, "duplicate": False,
            "message": "已入队 OCR；完成后自动建立语义索引", "request_id": rid}


# ================= 索引（内部回调 + 兼容入口）=================
async def resolve_uploaded_classification(doc_id: int) -> int:
    """回查上传时暂存的密级，落定到真实 doc_id 上。

    Paperless 与自研表同处一个 PostgreSQL（schema archive），其
    documents_document.checksum 即上传原始文件的 sha256，与上传接口暂存的 key 同源，
    因此可直接回查用户当时选择的密级。Paperless 的 REST 文档详情并不暴露 checksum
    字段，故这里走数据库直查而非上游 API。

    查不到则取默认「内部(1)」——比「公开(0)」更保守，符合 fail-closed 的默认取向。
    """
    try:
        with get_pg() as conn:
            row = conn.execute(
                "SELECT checksum FROM archive.documents_document WHERE id=%s",
                (doc_id,)).fetchone()
        checksum = row[0] if row and row[0] else None
    except Exception as e:  # noqa: BLE001
        log.warning("回查上传密级失败（取默认内部）doc_id=%s: %s", doc_id, e)
        return 1
    if not checksum:
        return 1
    try:
        with get_pg() as conn:
            row = conn.execute(
                "SELECT classification FROM archive.pending_classification"
                " WHERE content_sha=%s", (checksum,)).fetchone()
        if row:
            with get_pg() as conn:
                conn.execute(
                    "DELETE FROM archive.pending_classification WHERE content_sha=%s",
                    (checksum,))
            log.info("落定上传密级 doc_id=%s classification=%s", doc_id, row[0])
            return int(row[0])
    except Exception as e:  # noqa: BLE001
        log.warning("读取暂存密级失败（取默认内部）doc_id=%s: %s", doc_id, e)
    return 1


@app.post("/internal/index")
async def internal_index(request: Request):
    """Paperless post-consume 回调入口（X-Internal-Token 保护）。"""
    if request.headers.get("X-Internal-Token") != s.internal_token:
        raise HTTPException(status_code=403, detail="内部接口拒绝访问")
    body = await request.json()
    doc_id, op = int(body["doc_id"]), body.get("op", "upsert")
    # 入库即登记密级（fail-closed 前提：每份档案必有密级记录，默认内部=1）。
    # 不依赖索引器异步回填，避免「OCR 完成、索引成功，却检索不到」。
    level = await resolve_uploaded_classification(doc_id)
    with get_pg() as conn:
        conn.execute(
            "INSERT INTO archive.doc_security(doc_id,classification,owner_dept,tenant_id)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (doc_id) DO NOTHING",
            (doc_id, level, None, s.tenant_id))
        conn.execute("INSERT INTO archive.index_tasks(doc_id,op) VALUES (%s,%s)",
                     (doc_id, op))
    return {"status": "accepted", "doc_id": doc_id, "op": op, "classification": level}


@app.post("/api/v1/index/{doc_id}", response_model=TaskAccepted)
async def index_one(doc_id: int, request: Request, u: UserCtx = Depends(current_user)):
    if not has_role(u.roles, ROLE_ARCHIVIST, ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="需要档案员或管理员权限")
    with get_pg() as conn:
        conn.execute("INSERT INTO archive.index_tasks(doc_id,op) VALUES (%s,'upsert')", (doc_id,))
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="reindex", resource_type="document",
        resource_id=str(doc_id), decision="allow", status_code=202,
        ip=_client_ip(request), tenant_id=s.tenant_id)
    return TaskAccepted(request_id=request_id_var.get(), task_id=doc_id)


@app.post("/api/v1/index", response_model=TaskAccepted)
async def index_all(request: Request, u: UserCtx = Depends(admin_required)):
    """对账式重建：入队 -1 标记，indexer 异步执行，不再阻塞 HTTP。"""
    with get_pg() as conn:
        conn.execute("INSERT INTO archive.index_tasks(doc_id,op) VALUES (-1,'upsert')")
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="reindex", resource_type="system", resource_id="all",
        decision="allow", status_code=202, ip=_client_ip(request), tenant_id=s.tenant_id)
    return TaskAccepted(request_id=request_id_var.get(), task_id=-1)


# ================= 统计 =================
@app.get("/api/v1/stats")
async def stats(u: UserCtx = Depends(current_user)):
    try:
        data = await up.pl_get("/api/documents/", {"page_size": 1})
        total = data.get("count", 0)
    except Exception:
        total = None
    with get_pg() as conn:
        chunks = conn.execute("SELECT count(*) FROM archive_vec.chunks").fetchone()[0]
        docs = conn.execute("SELECT count(*) FROM archive_vec.docs").fetchone()[0]
        pending = conn.execute(
            "SELECT count(*) FROM archive.index_tasks WHERE status='pending'").fetchone()[0]
        dead = conn.execute(
            "SELECT count(*) FROM archive.index_tasks WHERE status='dead'").fetchone()[0]
    return {"total_documents": total,
            "semantic_index": {"documents": docs, "chunks": chunks},
            "index_queue": {"pending": pending, "dead": dead},
            "audit_dropped": audit_mod.dropped_count()}


# ================= 管理面 =================
@app.get("/api/v1/admin/users")
async def list_users(u: UserCtx = Depends(admin_required)):
    with get_pg() as conn:
        rows = conn.execute(
            "SELECT u.id,u.username,u.display_name,u.dept,u.clearance_level,u.status,"
            "COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL),'{}') roles "
            "FROM archive.users u LEFT JOIN archive.user_roles ur ON ur.user_id=u.id "
            "LEFT JOIN archive.roles r ON r.id=ur.role_id GROUP BY u.id ORDER BY u.id").fetchall()
    return {"users": [{"id": r[0], "username": r[1], "display_name": r[2], "dept": r[3],
                       "clearance": r[4], "status": r[5], "roles": r[6]} for r in rows]}


@app.post("/api/v1/admin/users")
async def create_user(request: Request, username: str = Form(...),
                      password: str = Form(...), display_name: str = Form(""),
                      dept: str = Form(""), clearance: int = Form(0, ge=0, le=3),
                      roles: str = Form(ROLE_VIEWER), u: UserCtx = Depends(admin_required)):
    with get_pg() as conn:
        if conn.execute("SELECT 1 FROM archive.users WHERE username=%s",
                        (username,)).fetchone():
            raise HTTPException(status_code=409, detail="用户名已存在")
        conn.execute(
            "INSERT INTO archive.users(username,pwd_hash,display_name,dept,clearance_level,status)"
            " VALUES (%s,%s,%s,%s,%s,'active')",
            (username, hash_password(password), display_name, dept, clearance))
        uid = conn.execute("SELECT id FROM archive.users WHERE username=%s",
                           (username,)).fetchone()[0]
        for rname in [x.strip() for x in roles.split(",") if x.strip() in ALL_ROLES]:
            conn.execute(
                "INSERT INTO archive.user_roles(user_id,role_id)"
                " SELECT %s,id FROM archive.roles WHERE name=%s ON CONFLICT DO NOTHING",
                (uid, rname))
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="permission_change", resource_type="user",
        resource_id=username, decision="allow", status_code=200, ip=_client_ip(request),
        detail={"roles": roles, "clearance": clearance}, tenant_id=s.tenant_id)
    return {"status": "ok", "user_id": uid}


@app.patch("/api/v1/admin/users/{user_id}")
async def update_user(user_id: int, request: Request,
                      clearance: Optional[int] = Form(None, ge=0, le=3),
                      status: Optional[str] = Form(None),
                      roles: Optional[str] = Form(None),
                      password: Optional[str] = Form(None),
                      u: UserCtx = Depends(admin_required)):
    sets, vals = [], []
    if clearance is not None:
        sets.append("clearance_level=%s")
        vals.append(clearance)
    if status in ("active", "disabled"):
        sets.append("status=%s")
        vals.append(status)
    if password:
        sets.append("pwd_hash=%s")
        vals.append(hash_password(password))
    if sets:
        vals.append(user_id)
        with get_pg() as conn:
            conn.execute(f"UPDATE archive.users SET {','.join(sets)} WHERE id=%s",
                         tuple(vals))
    if roles is not None:
        with get_pg() as conn:
            conn.execute("DELETE FROM archive.user_roles WHERE user_id=%s", (user_id,))
            for rname in [x.strip() for x in roles.split(",") if x.strip() in ALL_ROLES]:
                conn.execute(
                    "INSERT INTO archive.user_roles(user_id,role_id)"
                    " SELECT %s,id FROM archive.roles WHERE name=%s ON CONFLICT DO NOTHING",
                    (user_id, rname))
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="permission_change", resource_type="user",
        resource_id=str(user_id), decision="allow", status_code=200,
        ip=_client_ip(request),
        detail={"roles": roles, "clearance": clearance, "status": status},
        tenant_id=s.tenant_id)
    return {"status": "ok"}


@app.get("/api/v1/admin/audit-logs")
async def audit_logs(request: Request, action: Optional[str] = None,
                     decision: Optional[str] = None,
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                     u: UserCtx = Depends(current_user)):
    if not has_role(u.roles, ROLE_ADMIN, ROLE_AUDITOR):
        raise HTTPException(status_code=403, detail="需要审计员或管理员权限")
    conds, vals = [], []
    if action:
        conds.append("action=%s")
        vals.append(action)
    if decision in ("allow", "deny"):
        conds.append("decision=%s")
        vals.append(decision)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with get_pg() as conn:
        rows = conn.execute(
            f"SELECT id,ts,request_id,actor_name,action,resource_type,resource_id,"
            f"classification,decision,status_code,latency_ms,ip,detail "
            f"FROM archive.audit_logs {where} ORDER BY ts DESC LIMIT %s OFFSET %s",
            tuple(vals + [page_size, (page - 1) * page_size])).fetchall()
        total = conn.execute(f"SELECT count(*) FROM archive.audit_logs {where}",
                             tuple(vals)).fetchone()[0]
    return {"total": total, "page": page, "logs": [
        {"id": r[0], "ts": str(r[1]), "request_id": r[2], "actor": r[3], "action": r[4],
         "resource_type": r[5], "resource_id": r[6], "classification": r[7],
         "decision": r[8], "status_code": r[9], "latency_ms": r[10], "ip": r[11],
         "detail": r[12]} for r in rows]}


@app.get("/api/v1/admin/docs/{doc_id}/security")
async def get_doc_security(doc_id: int, u: UserCtx = Depends(admin_required)):
    return load_doc_security([doc_id]).get(doc_id, {})


@app.put("/api/v1/admin/docs/{doc_id}/security")
async def set_doc_security(doc_id: int, request: Request,
                           classification: int = Form(..., ge=0, le=3),
                           owner_dept: Optional[str] = Form(None),
                           u: UserCtx = Depends(admin_required)):
    with get_pg() as conn:
        conn.execute(
            "INSERT INTO archive.doc_security(doc_id,classification,owner_dept,tenant_id)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (doc_id) DO UPDATE SET"
            " classification=EXCLUDED.classification, owner_dept=EXCLUDED.owner_dept,"
            " updated_at=now()",
            (doc_id, classification, owner_dept, s.tenant_id))
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="permission_change", resource_type="document",
        resource_id=str(doc_id), classification=classification, decision="allow",
        status_code=200, ip=_client_ip(request),
        detail={"classification": classification}, tenant_id=s.tenant_id)
    return {"status": "ok", "doc_id": doc_id, "classification": classification}


# ================= 管理员备份（逻辑全量导出 + media/consume tar + SHA256 清单）=================
@app.post("/api/v1/admin/backup")
async def admin_backup(request: Request, u: UserCtx = Depends(admin_required)):
    """在线逻辑备份：逐表 COPY TO STDOUT 导出 archive/archive_vec + 打包 media/consume。

    不依赖 pg_dump：官方 pg_dump 为 musl 静态链，与 glibc 网关镜像混用存在动态库风险。
    物理级备份（pg_dump -Fc，含 schema 与索引）由宿主机 scripts/backup.sh 负责，
    二者互补：本端点供运维台一键触发，适合日常增量留档。
    """
    import datetime
    import gzip
    import subprocess

    out_dir = os.path.join(os.getenv("DATA_DIR", "/srv/data"), "backups")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dump_path = os.path.join(out_dir, f"logical-{stamp}.sql.gz")

    def q(ident: str) -> str:
        return '"' + ident.replace('"', '""') + '"'

    rows_total = 0
    with get_pg() as conn:
        schemas = [r[0] for r in conn.execute(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname IN ('archive','archive_vec') ORDER BY nspname").fetchall()]
        with gzip.open(dump_path, "wt", encoding="utf-8", newline="") as gz:
            gz.write(f"-- archive logical backup {stamp}\n"
                     "SET client_encoding='UTF8';\nSET standard_conforming_strings=on;\n")
            for schema in schemas:
                gz.write(f"CREATE SCHEMA IF NOT EXISTS {q(schema)};\n")
                tables = [r[0] for r in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname=%s "
                    "ORDER BY tablename", (schema,)).fetchall()]
                for t in tables:
                    with conn.cursor() as cur:
                        with cur.copy(f"COPY {q(schema)}.{q(t)} TO STDOUT") as cp:
                            # psycopg3 每块可能是 bytes / memoryview / str，统一解码
                            body = "".join(
                                bytes(blk).decode("utf-8") if not isinstance(blk, str)
                                else blk for blk in cp)
                    if not body:
                        continue
                    rows_total += body.count("\n")
                    gz.write(f"\n-- {schema}.{t}\n"
                             f"COPY {q(schema)}.{q(t)} FROM stdin;\n{body}\\.\n")
    size_mb = round(os.path.getsize(dump_path) / 1048576, 2)

    files = [dump_path]
    for label, src in (("media", "/srv/media"), ("consume", "/srv/consume")):
        tar_path = os.path.join(out_dir, f"{label}-{stamp}.tar.gz")
        if os.path.isdir(src):
            subprocess.run(["tar", "czf", tar_path, "-C", os.path.dirname(src),
                            os.path.basename(src)], check=True, timeout=600)
            files.append(tar_path)
    hashes = {}
    for f in files:
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        hashes[os.path.basename(f)] = h.hexdigest()
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="backup", resource_type="system", resource_id=stamp,
        decision="allow", status_code=200, ip=_client_ip(request),
        detail={"files": list(hashes), "rows": rows_total}, tenant_id=s.tenant_id)
    return {"status": "ok", "stamp": stamp, "size_mb": size_mb,
            "rows": rows_total, "sha256": hashes}


# ================= 扫描增强（对齐 CamScanner/Lens：裁边/透视校正/去阴影/增强）=================
@app.post("/api/v1/scan/enhance")
async def scan_enhance(request: Request, file: UploadFile = File(...),
                       mode: str = Form("enhance"),
                       u: UserCtx = Depends(current_user)):
    """上传拍照原图 → 服务端 OpenCV 增强 → 返回处理后 JPG。

    mode: enhance(去阴影+对比度) / gray(灰度文档) / bw(黑白二值) / orig(仅裁边校正)
    """
    if not has_role(u.roles, ROLE_ARCHIVIST, ROLE_ADMIN, ROLE_VIEWER):
        raise HTTPException(status_code=403, detail="无权使用扫描增强")
    from gateway.scan_enhance import enhance_bytes  # 延迟导入，避免常驻开销

    data = await file.read()
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片过大")
    try:
        out = await asyncio.to_thread(enhance_bytes, data, mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await audit_mod.write_audit(
        request_id=request_id_var.get(), actor_id=u.id, actor_name=u.username,
        actor_roles=u.roles, action="scan_enhance", resource_type="image",
        decision="allow", status_code=200, ip=_client_ip(request),
        detail={"mode": mode, "bytes_in": len(data)}, tenant_id=s.tenant_id)
    return StreamingResponse(iter([out]), media_type="image/jpeg")


# ================= 静态前端（同源托管，最后挂载）=================
_static = os.getenv("STATIC_DIR", "/srv/static")
if os.path.isdir(_static) and os.listdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
