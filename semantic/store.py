"""向量持久层：PG bytea 主 / SQLite 降级。统一接口。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

import numpy as np

from shared.db import get_pg, get_vec_db, vec_is_pg
from shared.settings import get_settings


@dataclass
class VecDoc:
    doc_id: int
    title: Optional[str]
    created: Optional[str]
    asn: Optional[int]
    content_sha: Optional[str] = None


class VectorStore:
    def init_schema(self) -> None: ...
    def upsert_doc(self, doc: VecDoc) -> None: ...
    def replace_chunks(self, doc_id: int, title: str, chunks: list[str],
                       vectors: np.ndarray, chunk_indexes: list[int]) -> None: ...
    def delete_doc(self, doc_id: int) -> None: ...
    def load_all(self) -> Optional[tuple[list[dict], np.ndarray]]: ...
    def stats(self) -> dict: ...
    def get_doc_sha(self, doc_id: int) -> Optional[str]: return None
    def mark_synced(self, doc_id: int, sha: str) -> None:
        """记录同步状态（轮询 diff 用），默认实现为空。"""
        return None
    def active_doc_ids(self) -> set[int]:
        """已索引的活跃 doc_id 集合（删除对账用）。"""
        return set()


class PgVectorStore(VectorStore):
    def init_schema(self) -> None:
        with get_pg() as conn:
            for ddl in (
                """CREATE SCHEMA IF NOT EXISTS archive_vec""",
                """CREATE TABLE IF NOT EXISTS archive_vec.docs(
                       doc_id INT PRIMARY KEY, title TEXT, created TEXT, asn INT,
                       tenant_id TEXT NOT NULL DEFAULT 'default', content_sha TEXT,
                       indexed_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
                """CREATE TABLE IF NOT EXISTS archive_vec.chunks(
                       chunk_id BIGSERIAL PRIMARY KEY, doc_id INT NOT NULL,
                       doc_title TEXT, chunk_index INT, text TEXT,
                       embedding BYTEA NOT NULL,
                       tenant_id TEXT NOT NULL DEFAULT 'default')""",
            ):
                conn.execute(ddl)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON archive_vec.chunks(doc_id)")
            except Exception:
                pass

    def upsert_doc(self, doc: VecDoc) -> None:
        with get_pg() as conn:
            conn.execute(
                "INSERT INTO archive_vec.docs(doc_id,title,created,asn,content_sha,tenant_id)"
                " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (doc_id) DO UPDATE SET"
                " title=EXCLUDED.title, created=EXCLUDED.created, asn=EXCLUDED.asn,"
                " content_sha=EXCLUDED.content_sha, indexed_at=now()",
                (doc.doc_id, doc.title, doc.created, doc.asn, doc.content_sha,
                 get_settings().tenant_id))

    def get_doc_sha(self, doc_id: int) -> Optional[str]:
        with get_pg() as conn:
            row = conn.execute("SELECT content_sha FROM archive_vec.docs WHERE doc_id=%s",
                               (doc_id,)).fetchone()
        return row[0] if row else None

    def mark_synced(self, doc_id: int, sha: str) -> None:
        with get_pg() as conn:
            conn.execute(
                "INSERT INTO archive.doc_sync_state(doc_id,content_sha,state,updated_at)"
                " VALUES (%s,%s,'indexed',now()) ON CONFLICT (doc_id) DO UPDATE SET"
                " content_sha=EXCLUDED.content_sha, state='indexed', updated_at=now()",
                (doc_id, sha))

    def active_doc_ids(self) -> set[int]:
        with get_pg() as conn:
            rows = conn.execute("SELECT doc_id FROM archive_vec.docs").fetchall()
        return {r[0] for r in rows}

    def replace_chunks(self, doc_id: int, title: str, chunks: list[str],
                       vectors: np.ndarray, chunk_indexes: list[int]) -> None:
        with get_pg() as conn:
            conn.execute("DELETE FROM archive_vec.chunks WHERE doc_id=%s", (doc_id,))
            for i, (txt, vec, idx) in enumerate(zip(chunks, vectors, chunk_indexes)):
                conn.execute(
                    "INSERT INTO archive_vec.chunks(doc_id,doc_title,chunk_index,text,"
                    "embedding,tenant_id) VALUES (%s,%s,%s,%s,%s,%s)",
                    (doc_id, title, int(idx), txt,
                     np.asarray(vec, dtype=np.float32).tobytes(),
                     get_settings().tenant_id))

    def delete_doc(self, doc_id: int) -> None:
        with get_pg() as conn:
            conn.execute("DELETE FROM archive_vec.chunks WHERE doc_id=%s", (doc_id,))
            conn.execute("DELETE FROM archive_vec.docs WHERE doc_id=%s", (doc_id,))
            conn.execute("DELETE FROM archive.doc_sync_state WHERE doc_id=%s", (doc_id,))

    def load_all(self):
        with get_pg() as conn:
            rows = conn.execute(
                "SELECT c.chunk_id, c.doc_id, c.doc_title, c.chunk_index, c.text,"
                " c.embedding, d.asn, d.created FROM archive_vec.chunks c"
                " LEFT JOIN archive_vec.docs d ON d.doc_id=c.doc_id"
                " ORDER BY c.chunk_id").fetchall()
        if not rows:
            return None
        mat = np.vstack([np.frombuffer(r[5], dtype=np.float32) for r in rows])
        metas = [{"chunk_id": r[0], "doc_id": r[1], "doc_title": r[2],
                  "chunk_index": r[3], "text": r[4], "asn": r[6], "created": r[7]}
                 for r in rows]
        return metas, mat

    def stats(self) -> dict:
        with get_pg() as conn:
            chunks = conn.execute("SELECT count(*) FROM archive_vec.chunks").fetchone()[0]
            docs = conn.execute("SELECT count(*) FROM archive_vec.docs").fetchone()[0]
        return {"backend": "pg", "chunks": chunks, "documents": docs}


class SqliteVectorStore(VectorStore):
    def init_schema(self) -> None:
        with get_vec_db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chunks(
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL, doc_title TEXT, chunk_index INTEGER,
                    text TEXT, embedding BLOB);
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
                CREATE TABLE IF NOT EXISTS docs(
                    doc_id INTEGER PRIMARY KEY, title TEXT, created TEXT, asn INTEGER,
                    content_sha TEXT, indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
                """)

    def upsert_doc(self, doc: VecDoc) -> None:
        with get_vec_db() as db:
            db.execute("INSERT OR REPLACE INTO docs(doc_id,title,created,asn,content_sha)"
                       " VALUES (?,?,?,?,?)",
                       (doc.doc_id, doc.title, doc.created, doc.asn, doc.content_sha))

    def get_doc_sha(self, doc_id: int) -> Optional[str]:
        with get_vec_db() as db:
            row = db.execute("SELECT content_sha FROM docs WHERE doc_id=?",
                             (doc_id,)).fetchone()
        return row["content_sha"] if row else None

    def mark_synced(self, doc_id: int, sha: str) -> None:
        pass  # SQLite 降级模式不维护同步状态表

    def active_doc_ids(self) -> set[int]:
        with get_vec_db() as db:
            rows = db.execute("SELECT doc_id FROM docs").fetchall()
        return {r["doc_id"] for r in rows}

    def replace_chunks(self, doc_id: int, title: str, chunks: list[str],
                       vectors: np.ndarray, chunk_indexes: list[int]) -> None:
        with get_vec_db() as db:
            db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            for txt, vec, idx in zip(chunks, vectors, chunk_indexes):
                db.execute(
                    "INSERT INTO chunks(doc_id,doc_title,chunk_index,text,embedding)"
                    " VALUES (?,?,?,?,?)",
                    (doc_id, title, int(idx), txt,
                     np.asarray(vec, dtype=np.float32).tobytes()))

    def delete_doc(self, doc_id: int) -> None:
        with get_vec_db() as db:
            db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            db.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))

    def load_all(self):
        with get_vec_db() as db:
            rows = db.execute(
                "SELECT c.chunk_id, c.doc_id, c.doc_title, c.chunk_index, c.text,"
                " c.embedding, d.asn, d.created FROM chunks c"
                " LEFT JOIN docs d ON d.doc_id=c.doc_id ORDER BY c.chunk_id").fetchall()
        if not rows:
            return None
        mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        metas = [{"chunk_id": r["chunk_id"], "doc_id": r["doc_id"],
                  "doc_title": r["doc_title"], "chunk_index": r["chunk_index"],
                  "text": r["text"], "asn": r["asn"], "created": r["created"]}
                 for r in rows]
        return metas, mat

    def stats(self) -> dict:
        with get_vec_db() as db:
            chunks = db.execute("SELECT count(*) c FROM chunks").fetchone()["c"]
            docs = db.execute("SELECT count(*) c FROM docs").fetchone()["c"]
        return {"backend": "sqlite", "chunks": chunks, "documents": docs}


def get_store() -> VectorStore:
    store: VectorStore = PgVectorStore() if vec_is_pg() else SqliteVectorStore()
    return store
