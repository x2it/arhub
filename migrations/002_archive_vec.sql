-- 002_archive_vec.sql : 向量存储（PG bytea 持久化，常驻内存 numpy 检索的持久层）。
-- 取舍：单机内网 ≤3万份×512维 ≈ 180MB 内存，暴力精确检索 5~20ms，
-- 不引入 pgvector/独立向量库；一次 pg_dump 可带走全部数据。

CREATE TABLE IF NOT EXISTS archive_vec.docs (
    doc_id      INT PRIMARY KEY,
    title       TEXT,
    created     TEXT,
    asn         INT,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    content_sha TEXT,
    indexed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS archive_vec.chunks (
    chunk_id    BIGSERIAL PRIMARY KEY,
    doc_id      INT NOT NULL,
    doc_title   TEXT,
    chunk_index INT,
    text        TEXT,
    embedding   BYTEA NOT NULL,   -- float32 原始字节，512 维
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON archive_vec.chunks(doc_id);
