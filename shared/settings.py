"""集中配置：收编原型全部硬编码（端口/超时/RRF_K/切片/密钥/限流）。"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 上游服务 ----
    paperless_url: str = "http://webserver:8000"
    semantic_url: str = "http://semantic:8600"
    redis_url: str = "redis://broker:6379/1"
    db_dsn: str = "postgresql://archive:archive@db:5432/archive"

    # ---- 密钥（启动期随机生成，绝不入库） ----
    jwt_secret: str = ""
    internal_token: str = ""
    jwt_expire_minutes: int = 720

    # ---- 网络 ----
    gateway_port: int = 8700
    semantic_port: int = 8600
    cors_origins: str = ""          # 逗号分隔白名单；空 = 同源部署
    tenant_id: str = "default"

    # ---- 超时分层（秒）----
    timeout_connect: float = 3.0
    timeout_keyword: float = 10.0
    timeout_semantic: float = 5.0
    timeout_download: float = 30.0
    timeout_upload: float = 120.0
    timeout_admin: float = 15.0

    # ---- 重试/熔断/限流 ----
    retry_attempts: int = 2
    circuit_fail_threshold: int = 5
    circuit_reset_seconds: int = 30
    rate_limit_per_min: int = 600

    # ---- 上传 ----
    max_upload_mb: int = 100

    # ---- 检索 ----
    rrf_k: int = 60
    default_top_k: int = 10
    max_top_k: int = 100
    # 语义召回的最低余弦相似度 + 相对最佳命中的允许落差。
    # 无阈值时库里文档少会「搜什么都返回同一批档案」，实测相关查询 top1 ≥ 0.45、
    # 无关查询 top1 ≤ 0.38，0.40 可干净切开；rel_margin 保留与最佳结果相近的弱相关项。
    semantic_min_score: float = 0.40
    semantic_rel_margin: float = 0.18

    # ---- 语义索引 ----
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    chunk_size: int = 200
    chunk_overlap: int = 50
    embed_batch_size: int = 32
    vector_backend: str = "pg"      # pg | sqlite
    data_dir: str = "/data"

    # ---- 多租户预留 ----
    tenant_default: str = "default"

    # ---- 静态前端 ----
    static_dir: str = "/srv/static"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
