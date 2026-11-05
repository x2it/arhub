# AGENTS.md · 给 AI Agent 的项目地图

> 这份文件可以整份交给外部智能体：读完后应能自行部署、调用、排障，不需要额外上下文。
> 更详细的人读文档见 [README.md](README.md) 与 [DEPLOY.md](DEPLOY.md)。

## 一句话

档枢 ArcHub 是单机内网私有化的智能档案中枢：扫描增强 → OCR 著录 → 密级管控 →
全文/语义检索 → 全量审计。6 个容器，只有网关 `8700` 对外暴露，无外部服务依赖。

## 部署（推荐路径）

```bash
git clone https://github.com/x2it/arhub.git
cd arhub
bash scripts/up.sh
```

前置条件：Linux x86_64、Docker 24+ 带 compose v2、`curl` / `tar` / `python3` / `sha256sum`、
至少 4 核 8GB 内存 15GB 磁盘、能拉取容器镜像与中文嵌入模型。`up.sh` 会依次生成随机密钥、
构建自研镜像、预下载 `BAAI/bge-small-zh-v1.5`、解除 Paperless 的 s6 `down` 标记、
补建中文检索索引，最后以 `/ready` 为门禁（最长 10 分钟）。**不要跳过 `/ready` 判定就说部署完成。**

无 git 环境可用分发包（Release v2.1.2 附件）：

```bash
BASE="https://github.com/x2it/arhub/releases/download/v2.1.2"
curl -fL --retry 3 -o arhub-deploy.bundle  "$BASE/arhub-deploy.bundle"
curl -fL --retry 3 -o arhub-deploy.sha256 "$BASE/arhub-deploy.sha256"
sha256sum -c arhub-deploy.sha256
mkdir -p /opt/arhub && tar -xzf arhub-deploy.bundle -C /opt/arhub
cd /opt/arhub/arhub-stack && bash scripts/up.sh
```

首次登录：用户 `admin`，口令在 `data/admin_credentials.txt`（首次启动随机生成，权限 600）。
全新部署为空库，`bash scripts/restore-demo.sh` 可导入 8 份虚构样例复现效果。

## API 调用速查

```bash
# 1. 取令牌
TOKEN=$(curl -s -X POST http://127.0.0.1:8700/api/v1/auth/login \
  -d "username=admin&password=$(cut -d' ' -f2 data/admin_credentials.txt)" | jq -r .access_token)
H="Authorization: Bearer $TOKEN"

# 2. 检索：mode = metadata | keyword | semantic | hybrid
curl -s -H "$H" "http://127.0.0.1:8700/api/v1/search?mode=hybrid&q=消防设施检查"

# 3. 文档列表 / 详情 / 下载
curl -s -H "$H" "http://127.0.0.1:8700/api/v1/documents?page=1&page_size=20"
curl -s -H "$H" "http://127.0.0.1:8700/api/v1/documents/12"
curl -s -H "$H" -o out.pdf "http://127.0.0.1:8700/api/v1/documents/12/download"

# 4. 上传（幂等键避免重复入库）
curl -s -H "$H" -F "file=@a.pdf" -F "title=会议纪要" -F "asn=1006" \
  http://127.0.0.1:8700/api/v1/upload

# 5. 扫描增强：mode = enhance | gray | bw | orig
curl -s -H "$H" -F "file=@photo.jpg" -F "mode=enhance" \
  http://127.0.0.1:8700/api/v1/scan/enhance -o enhanced.jpg

# 6. 健康与指标
curl -s http://127.0.0.1:8700/ready
curl -s http://127.0.0.1:8700/metrics
```

完整契约见 `http://127.0.0.1:8700/api/docs`（OpenAPI）。
若外层反向代理占用 `Authorization` 头，改用 `X-Arc-Token: <token>` 或 Cookie `arc_jwt`。
响应体始终带 `request_id`；`degraded: true` 表示语义路熔断、仅返回关键词结果。
密级为 fail-closed：无权限的记录不会出现在结果里，不要把它当 bug。

## 目录地图

| 路径 | 作用 |
|---|---|
| `gateway/app.py` | 全部 HTTP 路由；`deps.py` 是鉴权与密级裁决的唯一入口 |
| `gateway/scan_enhance.py` | OpenCV 扫描增强（四模式） |
| `semantic/` | 向量存取、常驻矩阵、编码、outbox worker 与对账 |
| `shared/settings.py` | 所有配置收编处，禁止散落硬编码 |
| `migrations/00x_*.sql` | initdb DDL，只增不改、必须幂等 |
| `frontend/` | Vue3 + Vite + TS，8 页面，`dist/` 由网关同源托管 |
| `scripts/` | 起栈、迁移、备份恢复、演示数据、测试与合规自检 |
| `deploy/` | 分发包生成与单端口发布反代 |

## 不可动摇的约束

- 不要把 `.env`、`data/`、`hf-cache/`、`backups/`、`frontend/dist/`、任何凭据或 `.dump` 提交进仓库。
- 新增任何检索路径都必须经过 `gateway/deps.py` 的 `post_filter_hits`；密级记录缺失一律拒绝。
- 所有端点必须带鉴权与审计，响应体带 `request_id`。
- 不要在代码或文档里写死本机绝对路径、内网地址或私有链接。
- 提交用 [Conventional Commits](https://www.conventionalcommits.org/) 并带 DCO 签名（`git commit -s`）。

## 排障速查

| 现象 | 处理 |
|---|---|
| semantic 反复重启、模型找不到 | `bash scripts/prefetch-model.sh`；离线环境需自备 `hf-cache/hub/` 模型目录 |
| webserver 日志停在 superuser 创建后 | `docker compose exec -T webserver bash -c 'rm -f /run/service/svc-*/down; s6-svc -u /run/service/svc-*'` |
| 中文关键词检索无结果 | `bash scripts/ensure-indexes.sh`（关键词走 PG `ILIKE + pg_trgm`，不依赖 Tantivy） |
| `/ready` 为 degraded | 网关会复用 `archive.sys_config` 中的 Paperless Token 并重试，稍候再探 |
| 前端改动不生效 | `cd frontend && pnpm install && pnpm build` |

## 相关文档

[README.md](README.md) · [DEPLOY.md](DEPLOY.md) · [CONTRIBUTING.md](CONTRIBUTING.md) ·
[SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md) · [NOTICE](NOTICE)
