"""依赖与越权裁决（系统命门）：

- current_user：JWT → 用户上下文；缺失/过期 → 401
- can_view：clearance >= classification 且角色/部门可见；**fail-closed**
  （密级记录缺失 → 不可见；任何解析失败 → 拒绝）
- post_filter_hits：四路召回合并后按 doc_id 批量过滤（语义路不经 Paperless，
  必须在网关统一裁决）

fail-closed 修复原型缺陷：gateway/app.py:146-149 tag 解析失败静默退化为返回全库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request

from gateway.security import ROLE_ADMIN, decode_token, has_role
from shared.db import get_pg
from shared.settings import get_settings


@dataclass
class UserCtx:
    id: int
    username: str
    roles: list[str] = field(default_factory=list)
    clearance: int = 0
    dept: str = ""
    tenant_id: str = "default"


def _bearer(request: Request) -> Optional[str]:
    """取 JWT，按可信度依次尝试三条通道。

    1) X-Arc-Token：前端显式携带，优先级最高；
    2) Cookie arc_jwt：覆盖 <a href> / <img src> 等无法带自定义头的原生请求；
    3) Authorization: Bearer：标准 API 调用（curl / 第三方集成）。

    之所以需要前两条：经由公共发布平台反代时，平台会占用 Authorization 头做自身
    访问控制，浏览器携带的 Bearer JWT 会被覆盖成一个网关无法验签的值。
    """
    def clean(v: str) -> Optional[str]:
        v = (v or "").strip()
        if v.lower().startswith("bearer "):
            v = v[7:].strip()
        return v or None

    custom = clean(request.headers.get("X-Arc-Token", ""))
    if custom:
        return custom
    cookie = clean(request.cookies.get("arc_jwt", ""))
    if cookie:
        return cookie
    return clean(request.headers.get("Authorization", ""))


async def current_user(request: Request) -> UserCtx:
    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="未登录或令牌缺失")
    claims = decode_token(token)
    if not claims:
        # 平台反代会占用 Authorization 头做自身访问控制，此时浏览器若未登录，
        # 该头会被当作令牌验签失败。文案需明确指向「重新登录」这一可自愈动作。
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    u = UserCtx(
        id=int(claims.get("sub", 0)),
        username=str(claims.get("username", "")),
        roles=list(claims.get("roles", [])),
        clearance=int(claims.get("clearance", 0)),
    )
    u.tenant_id = get_settings().tenant_id
    # 实时禁用校验：token 有效但用户已被禁用 → 拒绝
    with get_pg() as conn:
        row = conn.execute(
            "SELECT status FROM archive.users WHERE id=%s", (u.id,)
        ).fetchone()
    if row is None or row[0] != "active":
        raise HTTPException(status_code=401, detail="用户不可用")
    return u


async def admin_required(u: UserCtx = Depends(current_user)) -> UserCtx:
    if not has_role(u.roles, ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return u


def load_doc_security(doc_ids: list[int]) -> dict[int, dict]:
    """批量读密级。fail-closed：缺失的 doc_id 不在返回里 = 不可见。"""
    if not doc_ids:
        return {}
    with get_pg() as conn:
        rows = conn.execute(
            "SELECT doc_id, classification, owner_dept FROM archive.doc_security "
            "WHERE doc_id = ANY(%s)",
            (doc_ids,),
        ).fetchall()
    return {r[0]: {"classification": r[1], "owner_dept": r[2]} for r in rows}


def can_view(user: UserCtx, classification: int, owner_dept: str | None) -> bool:
    """裁决规则：clearance >= classification。部门可见性为增强项（预留）。"""
    return user.clearance >= classification


def post_filter_hits(user: UserCtx, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并召回后的统一越权过滤。密级缺失记录直接丢弃（fail-closed）。"""
    ids = [h.get("doc_id") for h in hits if isinstance(h.get("doc_id"), int)]
    sec = load_doc_security(ids)
    out = []
    for h in hits:
        s = sec.get(h.get("doc_id"))
        if not s:
            continue
        if can_view(user, s["classification"], s.get("owner_dept")):
            h["classification"] = s["classification"]
            out.append(h)
    return out


async def require_view_one(user: UserCtx, doc_id: int) -> dict:
    """单档案裁决：先查密级再回源；查不到密级 = 404（fail-closed）。"""
    sec = load_doc_security([doc_id]).get(doc_id)
    if not sec:
        raise HTTPException(status_code=404, detail="档案不存在或无密级记录")
    if not can_view(user, sec["classification"], sec.get("owner_dept")):
        raise HTTPException(status_code=403, detail="密级不足，禁止访问该档案")
    return sec
