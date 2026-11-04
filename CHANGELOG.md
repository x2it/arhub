# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [2.1.0] - 2026-11-05

采集页支持**选择本机图片**，同时修掉一个让新档案「已入库却语义检索不到」的
架构级一致性缺陷。

### 新增

- **采集页双入口**：拆分为「📸 拍照扫描」与「🖼️ 选择本机图片」两个独立入口。
  此前只有一个带 `capture="environment"` 的输入框，移动端浏览器会**强制拉起相机**，
  用户无法从相册或文件管理器中选取已拍好的图片——这是移动端最常见的劝退点。
  现在拍照入口保留 `capture`，选择入口不带该属性，移动端弹出相册/文件选择器，
  桌面端弹出磁盘选择器
- **统一图片归一化层**（`gateway/imaging.py`）：
  - 按**魔数**而非扩展名判定格式，放行 PDF / JPEG / PNG / WebP / TIFF / BMP / GIF / HEIC
    （iPhone 默认格式，此前会被 400 拒绝）
  - **EXIF Orientation 方向校正**：`cv2.imdecode` 会丢弃该标记，导致竖拍文稿在
    OCR 前整体旋转 90°，识别率骤降。现在统一用 Pillow `exif_transpose` 校正
  - **多页 TIFF 展开为多页 PDF**：TIFF 透传给 Paperless 只会留下首页
  - 无需变换的格式（PDF、无旋转标记的 JPEG/PNG）**原样透传**，保证上传幂等键
    （内容 SHA-256）稳定、不无谓损失画质
- **空白页检测接入前端**：服务端增强接口返回 `X-Blank-Page` 响应头，采集页据此
  标记并**自动跳过**误触的空白照片（此前 `is_blank` 是死代码，前端 `blank` 字段
  从未被赋值）
- 采集页明确区分「增强失败」与「空白页」两种状态，不再静默丢弃不支持的文件

### 修复

- **新档案「已入库但语义检索不到」**：常驻向量矩阵由 `semantic`（API）进程持有，
  而向量由 `indexer`（worker）进程写入 PostgreSQL——两者是**两个独立容器**。
  worker 写完向量后，API 进程的矩阵仍是启动时的快照，新档案只能被全文（关键词）
  检索命中，语义检索完全查不到，直到容器重启才恢复。修复为：
  - worker 写完向量后通过内部接口 `POST /internal/reload-matrix` **通知 API 进程
    亲自重载**常驻矩阵
  - API 进程新增 30 秒周期**自动刷新**兜底，通知丢失也能自愈
  - 同时修掉 `semantic/indexer.py` 中调用未导入的 `reload_matrix` 导致的
    `NameError: name 'reload_matrix' is not defined`（该异常被 try/except 吞掉，
    表现为矩阵刷新静默失败）
- `gateway/imaging.py` 修正两处自查发现的缺陷：EXIF 旋转判定必须在旋转**之前**
  进行（旋转后 orientation 已被清空）；多页帧必须在未旋转的原图上取
  （`exif_transpose` 走 `copy()` 会把多帧压成单帧，导致多页 TIFF 只剩首页）

### 变更

- 网关依赖新增 `pillow-heif==0.21.0`（HEIC 解码，manylinux 自带 libheif 二进制）
- 扫描增强错误提示扩展为「JPG/PNG/WEBP/TIFF/HEIC」

## [2.0.1] - 2026-11-05

修复「拍照扫描后查不到」。三个缺陷叠加导致该现象，逐个修复。

### 修复

- **网关上传只收 PDF**：采集页提交的是 JPG/PNG 扫描件，被 `PDF_MAGIC` 校验以 400 拒收，
  文件从未进入 Paperless。改为按魔数放行 PDF / JPEG / PNG，并按真实类型设置
  `Content-Type` 转发给 Paperless（Paperless 原生支持图片 OCR 入库）
- **post-consume 回调从未发出**：Paperless-ngx v3 调用回调脚本时不传位置参数，
  文档 ID 经 `DOCUMENT_ID` 环境变量下发；脚本沿用 v2 的 `$1` 写法取到空值后
  直接 `exit 0`。改为优先读 `DOCUMENT_ID`（保留 `$1` 兜底），并改用容器内确实存在的
  `curl`（官方镜像不带 `wget`）
- **文档入库即被隐藏**：`post_document` 返回的是任务 UUID 而非数字文档 ID，
  网关解析出的 `doc_id` 恒为 `null`，密级记录（fail-closed 前提）无法登记，
  文档虽已入库却被越权过滤拒于检索之外。改为在 `/internal/index` 回调中直接登记
  默认密级（内部 = 1），不再依赖上传接口同步拿到 `doc_id`

### 新增

- **上传时选定的密级可靠落定**：新增 `archive.pending_classification` 暂存表
  （`migrations/003_pending_classification.sql`），上传时按原始文件 sha256 暂存用户选定的密级；
  Paperless 的 `documents_document.checksum` 即原始文件 sha256，回调拿到真实 doc_id 后回查
  并落定密级（Paperless 的 REST 文档详情不暴露 `checksum`，故走数据库直查）。暂存记录
  24 小时未落定即清理；查不到时仍取默认「内部(1)」，比「公开(0)」保守，符合 fail-closed 取向
- `webserver` 服务注入 `GATEWAY_URL` 与 `INTERNAL_TOKEN`，打通采集回调链路
- 索引器新增 `reconcile_missing` 周期兜底：自动为漏登记密级的档案补齐密级与增量索引
- post-consume 失败诊断改写 stderr，可被 Paperless 消费日志捕获

## [2.0.0] - 2026-11-04

首个开源版本。相对 v1 原型为系统性重做，不是渐进迭代。

### 新增

- **基础设施**：SQLite → PostgreSQL 16；`archive` / `archive_vec` 双 schema；命名卷持久化；
  6 容器编排（db / broker / webserver / gateway / semantic / indexer），仅暴露网关 `8700`
- **安全底座**：JWT + bcrypt 原生哈希、四级 RBAC（viewer / archivist / auditor / admin）、
  四级密级（0 公开 / 1 内部 / 2 秘密 / 3 机密）、fail-closed 越权过滤（密级缺失即拒绝）、
  全量异步审计、请求 ID 全链路、令牌桶限流（429 带 `Retry-After`）
- **检索**：四种模式（metadata / keyword / semantic / hybrid），`asyncio.gather` 并发召回 +
  RRF 融合、语义路熔断器与 `degraded: true` 降级；中文关键词改由 PostgreSQL
  `ILIKE + pg_trgm` 承担（Paperless 的 Tantivy 不切中文）
- **语义内核**：BGE 中文嵌入、常驻内存 numpy 矩阵 + COW 原子替换 + `argpartition` top-k；
  向量以 `BYTEA` 持久化，一次 `pg_dump` 可带走全部数据
- **索引流水线**：outbox（`archive.index_tasks`）+ post-consume 回调 + 轮询 diff 兜底 +
  启动全量对账与删除同步；死信指数退避；索引时自动补建默认密级
- **扫描增强**（对齐 CamScanner / Microsoft Lens）：Canny 裁边、四点透视校正、
  高斯背景除法去阴影、CLAHE、Otsu 二值化、空白页检测；采集页原图/增强后对比预览与批量归档
- **Web 工作台**：Vue3 + Vite + TS，8 页面（登录 / 列表 / 详情 / 采集 / 统计 / 权限 / 审计 / 运维）
- **运维**：`/health` 与 `/ready` 分离且以 `/ready` 为起栈门禁；Prometheus `/metrics`；
  JSON 结构化日志；`scripts/backup.sh` / `restore.sh`（保留 7 天 + SHA256 清单）；
  在线备份端点与运维台全量对账
- **工程化**：13 项单元测试、24 项端到端断言、检索压测脚本、CI 串跑脚本、
  SQLite→PG 迁移脚本、可读的部署与开源文档

### 修复（开发期）

- PostgreSQL 绑定挂载 uid 属主漂移 → 改用命名卷
- 网关先于 Paperless 启动时 Token 引导失败且不重试 → 复用 `archive.sys_config` 落库 Token +
  指数退避重试，重启场景自恢复
- 官方 PG 入口以 `ON_ERROR_STOP=1` 执行 initdb，索引建在尚不存在的表上会中断初始化 →
  条件创建 + 启动后幂等补建
- 语义服务强制离线加载但模型未预载 → `scripts/prefetch-model.sh` 在起栈前下载
- Paperless 内部 s6 服务未自动拉起 → 起栈循环解 `down` 标记直到 API 就绪
- `SearchHit` 构造参数重复、`psycopg` COPY 返回 `memoryview`、连接池懒加载竞态、
  indexer 健康检查误配、顶栏用户名非响应式、公共反代占用 `Authorization` 头（改为三通道兼容）

### 安全

- 无默认口令：管理员口令首次启动随机生成并落盘 600 权限文件
- 上传 PDF magic 校验 + 流式大小限制 + 幂等键；下载响应带 `nosniff`
- 前端高亮片段白名单 sanitize，仅放行 `<mark>`

## [1.0.0] - 2026-10-25（未发布，原型）

- 8 份中文扫描件端到端原型：Paperless-ngx（SQLite）+ 外挂语义检索服务 + 统一网关
- 验证路线可行性，未包含鉴权、密级、审计、生产数据库与 Web 工作台

---

[2.0.1]: https://github.com/x2it/arhub/releases/tag/v2.0.1
[2.0.0]: https://github.com/x2it/arhub/releases/tag/v2.0.0
