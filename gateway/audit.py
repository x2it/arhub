"""审计：操作留痕（响应后异步落库，失败降级日志并计数）。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

log = logging.getLogger("gateway.audit")
_dropped = 0  # 落库失败计数（导出到 /metrics 的告警线索）


async def write_audit(
    *,
    request_id: str,
    actor_id: Optional[int],
    actor_name: str,
    actor_roles: list[str],
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    classification: Optional[int] = None,
    decision: str = "allow",
    status_code: int = 200,
    latency_ms: int = 0,
    ip: str = "",
    detail: dict | None = None,
    tenant_id: str = "default",
) -> None:
    payload = dict(
        request_id=request_id, actor_id=actor_id, actor_name=actor_name,
        actor_roles=actor_roles, action=action, resource_type=resource_type,
        resource_id=resource_id, classification=classification, decision=decision,
        status_code=status_code, latency_ms=latency_ms, ip=ip,
        detail=json.dumps(detail or {}, ensure_ascii=False), tenant_id=tenant_id,
    )
    try:
        await asyncio.to_thread(_insert_sync, payload)
    except Exception as e:  # 审计失败绝不能影响业务，但必须留下线索
        global _dropped
        _dropped += 1
        log.error("审计落库失败(%s): %s :: %s", _dropped, e, json.dumps(payload, ensure_ascii=False)[:500])


def _insert_sync(p: dict) -> None:
    from shared.db import get_pg

    with get_pg() as conn:
        conn.execute(
            "INSERT INTO archive.audit_logs(request_id,actor_id,actor_name,actor_roles,action,"
            "resource_type,resource_id,classification,decision,status_code,latency_ms,ip,detail,tenant_id) "
            "VALUES (%(request_id)s,%(actor_id)s,%(actor_name)s,%(actor_roles)s,%(action)s,"
            "%(resource_type)s,%(resource_id)s,%(classification)s,%(decision)s,%(status_code)s,"
            "%(latency_ms)s,%(ip)s,%(detail)s,%(tenant_id)s)",
            p,
        )


def dropped_count() -> int:
    return _dropped
