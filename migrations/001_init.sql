-- 001_init.sql : 自研业务库 DDL（PG 16）。由 postgres 容器首次初始化自动执行。

CREATE SCHEMA IF NOT EXISTS archive;
CREATE SCHEMA IF NOT EXISTS archive_vec;

-- ========= 用户与角色 =========
CREATE TABLE IF NOT EXISTS archive.users (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    pwd_hash        TEXT NOT NULL,
    display_name    TEXT,
    dept            TEXT,
    clearance_level INT  NOT NULL DEFAULT 0 CHECK (clearance_level BETWEEN 0 AND 3),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS archive.roles (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE  -- viewer / archivist / auditor / admin
);

CREATE TABLE IF NOT EXISTS archive.user_roles (
    user_id INT NOT NULL REFERENCES archive.users(id) ON DELETE CASCADE,
    role_id INT NOT NULL REFERENCES archive.roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ========= 档案密级（与 Paperless 自定义字段双向同步）=========
-- classification: 0 公开 / 1 内部 / 2 秘密 / 3 机密
CREATE TABLE IF NOT EXISTS archive.doc_security (
    doc_id         INT PRIMARY KEY,          -- = Paperless documents_document.id
    classification INT NOT NULL DEFAULT 1 CHECK (classification BETWEEN 0 AND 3),
    owner_dept     TEXT,
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_security_class ON archive.doc_security(classification);

-- ========= 审计日志（谁在何时查/下载/上传了哪份密件，不可抵赖）=========
CREATE TABLE IF NOT EXISTS archive.audit_logs (
    id             BIGSERIAL PRIMARY KEY,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id     TEXT NOT NULL,
    actor_id       INT,
    actor_name     TEXT,
    actor_roles    TEXT[],
    action         TEXT NOT NULL,            -- login/search/view/download/upload/permission_change/scan_enhance
    resource_type  TEXT,                     -- document/search/upload/system
    resource_id    TEXT,
    classification INT,
    decision       TEXT NOT NULL,            -- allow / deny
    status_code    INT,
    latency_ms     INT,
    ip             TEXT,
    detail         JSONB,
    tenant_id      TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_audit_actor   ON archive.audit_logs(actor_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON archive.audit_logs(action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_request ON archive.audit_logs(request_id);

-- ========= 上传幂等（Idempotency-Key / 内容哈希 → doc_id）=========
CREATE TABLE IF NOT EXISTS archive.upload_idem (
    idem_key    TEXT PRIMARY KEY,
    content_sha TEXT NOT NULL,
    doc_id      INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========= 索引任务 outbox（增量索引/删除同步，持久化+重试+死信）=========
CREATE TABLE IF NOT EXISTS archive.index_tasks (
    id           BIGSERIAL PRIMARY KEY,
    doc_id       INT NOT NULL,
    op           TEXT NOT NULL CHECK (op IN ('upsert','delete')),
    content_sha  TEXT,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','dead')),
    attempts     INT NOT NULL DEFAULT 0,
    last_error   TEXT,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tasks_pick ON archive.index_tasks(status, run_after);

-- ========= 文档同步状态（轮询 diff 兜底：Paperless modified vs 向量版本）=========
CREATE TABLE IF NOT EXISTS archive.doc_sync_state (
    doc_id      INT PRIMARY KEY,
    content_sha TEXT,
    pl_modified TIMESTAMPTZ,
    vec_version INT NOT NULL DEFAULT 0,
    state       TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','indexed','deleted')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========= 默认角色与种子用户 =========
INSERT INTO archive.roles(name) VALUES ('viewer'),('archivist'),('auditor'),('admin')
ON CONFLICT (name) DO NOTHING;

-- ========= 系统配置（服务间共享的运行时密钥：Paperless API Token 等）=========
CREATE TABLE IF NOT EXISTS archive.sys_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ========= 中文全文检索：Paperless 的 Tantivy 分词不支持中文，关键词路由 PG 承担 =========
-- 注意：initdb 阶段 Paperless 尚未 migrate，documents_document 表还不存在。
-- postgres 官方 entrypoint 以 ON_ERROR_STOP=1 执行本文件，直接 CREATE INDEX 会中断整个初始化，
-- 因此这里条件创建；部署完成后由 scripts/ensure-indexes.sh 再次幂等补建。
CREATE EXTENSION IF NOT EXISTS pg_trgm;
DO $$
BEGIN
    IF to_regclass('archive.documents_document') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_doc_title_trgm ON archive.documents_document USING gin (title gin_trgm_ops)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_doc_content_trgm ON archive.documents_document USING gin (content gin_trgm_ops)';
    END IF;
END $$;
