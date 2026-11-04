-- 003_pending_classification.sql : 上传时选定密级的暂存表（业务库 archive schema）。
--
-- 背景：Paperless 的 post_document 接口返回的是消费任务 UUID，而非数字文档 ID，
-- 真实 doc_id 要等 OCR 完成后由 post-consume 回调带回。因此上传接口在提交给
-- Paperless 之前，无法确定密级记录该挂在哪个 doc_id 上。
--
-- 方案：上传时先按「原始文件 sha256」暂存用户选定的密级；待回调拿到真实 doc_id 后，
-- 用 Paperless 的 documents_document.checksum（即原始文件的 sha256）回查并落定密级。
-- 依据：Paperless-ngx 的 documents_document.checksum 与上传文件内容的 sha256 一致。

CREATE TABLE IF NOT EXISTS archive.pending_classification (
    content_sha   TEXT PRIMARY KEY,
    classification INT NOT NULL DEFAULT 1 CHECK (classification BETWEEN 0 AND 3),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pending_classification_created
    ON archive.pending_classification (created_at);
