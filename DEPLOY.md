# 档枢 ArcHub · 分发包部署手册

> 面向「拿到绝对链接后自行下载部署」的执行方（人或其他智能体）。
> 本包是**完整可运行源码**，不是演示页：6 容器生产栈（PostgreSQL + Redis + Paperless-ngx + 网关 + 语义服务 + 索引器）+ Vue3 前端。

---

## 一、前置条件

| 项 | 要求 |
|---|---|
| 操作系统 | Linux（x86_64），Docker 24+ 且带 `docker compose` v2 插件 |
| 工具 | `bash`、`curl`、`tar`、`python3`、`sha256sum` |
| 资源 | ≥ 4 核 / 8GB 内存 / 15GB 磁盘（镜像约 2.5GB，模型约 90MB） |
| 网络 | 能拉取容器镜像（`ghcr.io` / 国内代理）与 HuggingFace 镜像站；**纯离线环境需自备镜像与模型缓存** |
| 端口 | `8700`（唯一对外端口），其余组件全在内网容器网络 |

> 本包**不含任何密钥**：`.env`、管理员口令、模型缓存、运行时数据均已剔除。首次启动 `scripts/up.sh` 会自动生成全套随机密钥（`.env`，600 权限）与管理员密码（`data/admin_credentials.txt`）。

## 二、获取项目（CLI）

**方式 A · 克隆仓库（推荐）**

```bash
git clone https://github.com/x2it/arhub.git arhub
cd arhub
```

**方式 B · 分发包（离线 / 无 git 环境）**

先自行生成并托管三件产物：

```bash
bash deploy/make_bundle.sh
# 产出 frontend/dist/arhub-deploy.bundle / arhub-deploy.sha256 / bootstrap.txt
# 把它们上传到任意 HTTP 静态托管：对象存储、Release、内网文件服务器均可
```

下载与校验：

```bash
BASE="https://github.com/x2it/arhub/releases/download/v2.2.0"
curl -fL --retry 3 -o arhub-deploy.bundle  "$BASE/arhub-deploy.bundle"
curl -fL --retry 3 -o arhub-deploy.sha256 "$BASE/arhub-deploy.sha256"
sha256sum -c arhub-deploy.sha256
mkdir -p /opt/arhub && tar -xzf arhub-deploy.bundle -C /opt/arhub
cd /opt/arhub/arhub-stack
```

一键版（下载 + 校验 + 解压，第二参数为下载地址）：

```bash
curl -fsSL "$BASE/bootstrap.txt" | bash -s -- /opt/arhub "$BASE"
cd /opt/arhub/arhub-stack && bash scripts/up.sh
```

> 部分托管平台的 WAF 会拦截 `.tar.gz` / `.sh` 后缀，所以产物命名为 **`.bundle`**、
> 引导脚本命名为 **`bootstrap.txt`**。它们的内容分别是 gzip 压缩的 tar 包和纯文本 shell 脚本，
> `tar -xzf` / `curl … | bash` 均可直接处理，无需改名。

## 三、启动

```bash
bash scripts/up.sh
```

脚本按 7 步执行，全程自动：

1. 生成 `.env` 全套随机密钥，设置数据目录权限；
2. 检查/拉取基础镜像（多源兜底）；
3. 构建自研镜像 `archive-gateway:2.0` / `archive-semantic:2.0`；
4. **预下载中文嵌入模型** `BAAI/bge-small-zh-v1.5` 到 `hf-cache/`（服务强制离线加载，缺此步必失败）；
5. 启动 6 容器，循环解除 Paperless 的 s6 `down` 标记直到 API 就绪；
6. 等 Paperless 建表后补建中文检索 `pg_trgm` 索引；
7. `/ready` 门禁（最长 10 分钟），全绿才宣布成功。

成功输出：

```
全栈就绪  http://127.0.0.1:8700
   管理员初始密码: data/admin_credentials.txt
```

浏览器打开 `http://<部署机IP>:8700`，用户 `admin`，口令见 `data/admin_credentials.txt`。**首次登录后立即改密**。

## 四、载入演示数据（可选）

全新部署是空库。包内附带 8 份中文扫描件与导出清单，可一键复现检索效果：

```bash
bash scripts/restore-demo.sh
```

该脚本执行 `document_importer` 导入、重建全文索引、补 trigram 索引，并让 indexer 自动补齐密级与语义向量。完成后即可用大白话检索（如「出差住酒店能报销多少钱」）。

## 五、验收

```bash
curl -s http://127.0.0.1:8700/ready          # 三依赖全绿
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8700/   # 200
python3 -m pytest tests/unit -q             # 13 项单元测试
python3 scripts/e2e_test.py                  # 24 项端到端断言（需先起栈）
python3 scripts/load_test.py 8 200           # 压测
bash scripts/backup.sh                       # 物理级备份（pg_dump -Fc + media/consume + SHA256）
```

## 六、运维与故障排查

| 现象 | 处理 |
|---|---|
| semantic 反复重启、报模型找不到 | 手动执行 `bash scripts/prefetch-model.sh`；离线环境需把 `models--BAAI--bge-small-zh-v1.5` 完整目录放入 `hf-cache/hub/` 后重启 |
| webserver 日志停在 superuser 创建后不动 | `up.sh` 已循环解 s6 标记；仍不行则手工执行 `docker compose exec -T webserver bash -c 'rm -f /run/service/svc-*/down; s6-svc -u /run/service/svc-*'` |
| 中文关键词检索无结果 | 执行 `bash scripts/ensure-indexes.sh`；关键词路由走 PostgreSQL `ILIKE + pg_trgm`，不依赖 Paperless 的 Tantivy 中文分词 |
| 镜像拉取慢/失败 | 改 `.env` 中 `PG_IMAGE` / `PAPERLESS_IMAGE` / `PYTHON_IMAGE` 为可用镜像源；pypi 源在 Dockerfile 内已指向清华源 |
| 网关 `/ready` 为 degraded | 网关会复用 `archive.sys_config` 中已落库的 Paperless Token 并重试换取；重启场景自恢复已内置 |
| 前端需改动 | `cd frontend && pnpm install && pnpm build`，产物 `frontend/dist` 由网关同源托管，无需重建镜像 |

备份/恢复：`scripts/backup.sh`（保留 7 天、SHA256 清单）、`scripts/restore.sh`、网关端点 `POST /api/v1/admin/backup`（在线逻辑导出）。

## 七、安全边界与生产化建议

- 仅 `8700` 暴露；PostgreSQL / Redis / Paperless / 语义服务全部位于 `internal` 网络，绕过网关直连被物理关闭。
- 越权过滤在网关统一执行（含语义检索路），密级缺失即拒绝（fail-closed）；四级 RBAC + 全量审计。
- 生产上线前：**改管理员口令**、配 HTTPS 反向代理与防火墙白名单、配置定时 `scripts/backup.sh`、把 `CORS_ORIGINS` 收敛到实际域名。
- 随包附带的 `export/` / `media/` / `samples/` 是演示数据，正式投产前清空 `media/documents` 并删除 `export/` 内容。
- `deploy/publish_proxy.py` 仅用于把 6 容器栈接入单端口发布通道做预览，**自建服务器不需要它**。

## 八、包内容

```
arhub-stack/
├── docker-compose.yml          # 6 服务编排
├── .env.example                # 配置模板（无密钥）
├── migrations/                 # initdb DDL（业务库 + 向量库 + 中文检索索引）
├── gateway/ semantic/ shared/  # 自研服务源码
├── frontend/                   # Vue3 源码 + 已构建 dist
├── docker/                     # 两个多阶段 Dockerfile
├── scripts/                    # up / 迁移 / 备份 / 恢复 / 压测 / 演示数据
├── tests/                      # 单元测试
├── tessdata/chi_sim.traineddata# 中文 OCR 语言包（compose 必需）
├── export/ media/ samples/     # 8 份演示档案（可选载入）
├── docs/                       # 实测截图
├── DEPLOY.md（本文件）README.md
└── MANIFEST.txt                # 本包文件清单与校验值
```

**未包含**：`.env`、`data/`、`hf-cache/`、`backups/`、`logs/`、`.git/`、`frontend/node_modules/`、任何 `.dump` / 凭据文件。
