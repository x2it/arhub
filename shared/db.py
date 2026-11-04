"""PostgreSQL 连接池 + 轻量 SQLite 降级。"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from shared.settings import get_settings

_pool_lock = threading.Lock()


def _is_pg() -> bool:
    return os.getenv("VECTOR_BACKEND", get_settings().vector_backend) == "pg"


_pool = None  # type: ignore


def _get_pool():
    """懒建连接池（线程安全：双重检查 + 锁）。"""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool
            _pool = ConnectionPool(
                get_settings().db_dsn, min_size=1, max_size=10,
                open=True, timeout=10, kwargs={"autocommit": True},
            )
    return _pool


@contextmanager
def get_pg() -> Iterator["object"]:
    """archive 业务库连接（psycopg_pool 托管）。"""
    with _get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """进程退出时优雅关闭连接池。"""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def vec_is_pg() -> bool:
    return _is_pg()


@contextmanager
def get_vec_db() -> Iterator["object"]:
    """向量持久层连接：PG 主 / SQLite 降级。"""
    if _is_pg():
        with get_pg() as conn:
            yield conn
        return
    import sqlite3

    from shared.settings import get_settings

    path = os.path.join(get_settings().data_dir, "semantic.db")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    try:
        yield db
        db.commit()
    finally:
        db.close()
