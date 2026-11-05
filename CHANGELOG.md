# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [2.3.0] - 2026-11-06

针对「名片、手写板能不能扫？现在只能扫 PDF / 只能扫印刷体吗？」的反馈，把
**手写与弱印刷 OCR** 能力补齐，并正面回答格式与字体边界。

### 澄清（已在界面与文档固化）
- **不限于 PDF**：采集页接受 `image/*` + `.heic/.heif/.pdf`，网关 `normalize_upload()`
  支持 JPG / PNG / HEIC / WebP / 多页 TIFF / BMP / GIF / PDF；手机拍照、选本机图、
  传文件均可。PDF 只是归档输出格式之一。
- **不限于印刷体**：Tesseract（`chi_sim+eng`）覆盖印刷体，名片 / 文档 / 白底黑字打印件
  识别率高；**中文手写之前完全扫不出**（Tesseract 无手写模型，用户真实手写照片输出 0 行）。

### 新增：本地 PaddleOCR 手写 OCR 兜底引擎
- 网关新增 `gateway/ocr_fallback.py`：纯本地 **RapidOCR（PaddleOCR 的 ONNX 实现）**，
  模型随 pip 包内置（det 4.6M + rec 11M + cls 0.6M），不联网、不出网、不需要密钥。
- `requirements-gateway.txt` 增加 `rapidocr-onnxruntime==1.4.4`；Dockerfile 强制
  `opencv-python-headless`，规避 rapidocr 拉入 GUI 版 opencv 导致的 `libxcb` 启动崩溃。
- **自动兜底**：`/internal/index`（post-consume 回调）在 Tesseract 主 OCR 为空时，
  异步用 PaddleOCR 对原件重新识别并写回 `content`、重建语义索引——上传手写照片即自动出字。
- **手动重处理**：`POST /api/v1/documents/{id}/ocr-reprocess`（档案员 / 管理员），
  强制用 PaddleOCR 重识别；详情页新增「手写OCR重处理」按钮。

### 验证
- 端到端：用户真实手写照片（JPG，非 PDF）上传 → Tesseract 空 → PaddleOCR 兜底写入 95 字
  （门牌号、租金等关键字段均命中）；检索 `2500` / `租房` 均命中 doc 29（keyword + semantic）。
- 单元 3/3、E2E 33/33、浏览器回归 62/62、新增浏览器验证 3/3、6 容器 healthy、CI success。

## [2.2.1] - 2026-11-06

针对「这么肉眼可见的情况为什么还是抓不到 OCR 文本」的第三次反馈，把**诊断说明**与
**补救闭环**一次补齐，不再让用户逐个现象反复来问。

排查确认：**OCR 引擎没有故障**。库内 8 份印刷文档正文 186~292 字全部正常识别；
3 份用户自采素材（翻拍屏幕 ×2、浅色压纹包装照片 ×1）正文均为 0。后者原件
3072×4096，灰度均值 242.7、仅 1.39% 像素灰度 <150、局部块对比度中位数 12.9，
被 `quality_flag()` 判为 `low-contrast`；Tesseract 在原图、2× 放大、CLAHE、Otsu、
自适应阈值、黑帽压纹增强、局部对比度拉伸七种策略下都只输出乱码碎片，
OCRmyPDF 丢弃低置信度结果属预期行为。页面上那些清晰的中文是**界面 UI 文字**，
不参与识别；OCR 只处理上传的那张图。

### 新增：OCR 正文补录（让「无文字素材」也能被检索）

- `PATCH /api/v1/documents/{id}/content`：档案员 / 管理员可补录或修订正文，
  写入 Paperless `content`、入队 `index_tasks` 异步重建语义向量、写审计留痕；
  viewer 越权返回 403，无密级记录仍 fail-closed 404。
- `Upstreams.pl_patch()`：写操作的 4xx / 连接异常统一转结构化错误，
  避免「界面提示成功但库里没变」。
- 详情页「OCR 全文」卡片新增补录 / 编辑按钮与编辑框，移动端按钮、输入框、
  保存 / 取消按钮均做适配；保存后即时刷新正文并提示索引正在后台重建。

### 改进：把「为什么」写进界面，而不是让用户来问

- 详情页空状态改为诊断式说明：明确「档案与拍摄原件都已完整入库，不是内容丢失」，
  点明 UI 文字不参与 OCR、浅色压纹 / 翻拍屏笔画在二值化时与背景一起消失的原理，
  并给出「重拍」或「补录正文」两条可操作路径。
- 列表页空状态文案同步，补充「在详情页补录正文」入口。

### 修复：公开分发包混入运行时上传目录

- `deploy/make_bundle.sh` 原先只排除 `.env` / `data/` 等，`media/`（用户实际扫描件）与
  `consume/`（上传暂存）会被打进 Release 附件，等同于把私有档案公开发布。本次排除
  这两个目录，并加入敏感兜底校验（命中即中止并删除产物）。
- 演示档案本就由 `export/` + `scripts/restore-demo.sh` 提供，`up.sh` 会自动重建空
  `media/`，全新部署不受影响。
- 同步替换 v2.2.0 Release 的历史附件，消除此前已公开的那批用户照片。

### 验证

- 单测 44/44（含新增 `tests/unit/test_content_patch.py` 5 例）、E2E 33/33、浏览器回归 62/62。
- 真实链路：doc 28 补录 57 字 → 索引任务 `done` → 全文检索与语义检索均命中 doc 28。

## [2.2.0] - 2026-11-05

针对「文件不可以在线预览」「总的内容只能显示 50 条」的系统性修复。排查确认这不是两个
孤立开关，而是**预览链路依赖浏览器插件 + 下载 MIME 标错 + 列表无分页 + 总数语义错误**
四处叠加，本次一次性把两条链路整条重做。

### 新增：在线预览（不依赖浏览器 PDF 插件）

- 详情页原先用 `<iframe src=*.pdf>` 依赖浏览器内置 PDF 阅读器；移动端 Edge / 微信 WebView
  会禁用阅读器或强制下载，用户只看到「已在 Microsoft Edge 中禁用 PDF 阅读器」。新增
  `gateway/preview.py`：用 **PDFium（pypdfium2，BSD-3-Clause / Apache-2.0）** 把归档 PDF
  逐页渲染成 JPEG，图片类原件复用 `imaging.iter_frames`（EXIF 校正 / 透明底压白 / 多帧展开）。
- 新增 `GET /api/v1/documents/{id}/preview?page=N&src=archive|original`，返回 JPEG 与
  `X-Page-Count` / `X-Page-Index` / `X-Original-Mime` 响应头，带 10 分钟 LRU 缓存与
  fail-closed 密级裁决、审计留痕。
- 前端抽出通用 `PreviewPane`：翻页、放大、归档版/拍摄原件切换、下载；图片走统一鉴权请求层
  （三通道 JWT + 401 统一登出），不再用裸 `<img src>` / `<iframe>`。新增 `/preview/:id`
  独立预览页（新窗口打开、无全局顶栏）。
- **空 OCR 文档仍可查看拍摄原件**：翻拍屏幕、空白页一眼可辨，不再只显示「（暂无 OCR 文本）」。

### 修复：下载与鉴权

- `original=true` 时 Paperless 返回的是 JPEG，后端却硬编码 `application/pdf`，这是预览失败的
  另一半原因。下载端点改为按魔数判定真实 MIME 并带上规范文件名。
- 所有下载改走 `downloadFile()`（基于统一鉴权请求层），不再用裸 `<a href>` 只靠 cookie
  单通道；登录态过期时同样会正确跳登录。

### 修复：列表无分页 / 50 条天花板

- 检索页原有原生 `<select>` 只有 5/10/20/50，`browse_documents` 又把 `top_k` 直接当
  `page_size`，无分页、无真实总数，超过上限的档案永远看不到。改为 **SQL 真分页**
  （`archive.documents_document` 与密级表同库 JOIN），返回库内可见档案总数、
  `total_pages`、`has_more`，前端用自定义每页条数控件 + 分页器翻完全库。
- `total` 语义修正：浏览模式是**可见档案总数**，不再等于本页条数；结果编号按页偏移。
- `max_top_k` 100 → 500，`/api/v1/documents` 单页上限 100 → 200。明确区分
  `top_k`（相关性召回上限）与 `page_size`（浏览分页每页条数）。
- 顺带修掉 `imaging.iter_frames` 多页 TIFF 只取首页的隐藏 bug（`exif_transpose` 的
  `copy()` 会压掉多帧，必须在未旋转原图上逐帧 seek）。

### 依赖与合规

- 预览渲染选 pypdfium2 而非 PyMuPDF：后者是 AGPL-3.0 / 商业双授权，与本项目 Apache-2.0
  的开源分发不兼容；PDFium 绑定为 BSD-3-Clause / Apache-2.0。NOTICE 已同步登记。

### 验证

单元测试 39/39 · 端到端断言 33/33 · 浏览器回归 62/62（桌面 + 375px，覆盖预览翻页、
放大、独立预览页、分页翻页、无 iframe、无横向溢出）· 公网真实浏览器实测通过。

## [2.1.2] - 2026-11-05

针对「已采集的文档内容都是空的」与移动端登录区排版的系统性修复。排查发现这不是单一故障，
而是**检索展示、语义召回、OCR 可观测性、页面布局**四处缺陷叠加，导致用户既看不到档案、
也搜不到自己的采集件。

### 修复

- **检索页首次加载显示「暂无结果」**：空检索词时前端直接 `return`、后端关键词路返回 `[]`，
  页面渲染「暂无结果，请调整检索条件」，让人误以为档案库是空的。新增**浏览模式**：
  空 `q` 时按入库时间倒序列出全部可见档案（仍走 fail-closed 密级过滤），响应带
  `browsing` 标记、`ocr_empty` 与 `content_length`，前端明确区分「未输入检索词」与「检索无命中」。
- **语义检索无相似度阈值，搜什么都返回全库**：原 `ResidentMatrix.search()` 无条件返回
  `top_k` 个近邻，向量库仅 8 篇文档时，连「今天天气怎么样」「asdfghjkl」都会把全库吐出
  并污染 hybrid 融合。新增绝对阈值 `0.40` + 相对最佳命中落差 `0.18`（实测相关查询
  top1 ≥ 0.45、无关查询 top1 ≤ 0.38），并按 `doc_id` 去重只保留最高分分块。
- **翻拍屏幕 / 文字过淡的图被增强洗成空文档**：`_remove_shadow`（背景除法）会把浅灰笔画
  与背景一起归一化，OCR 返回 `No text was found`，系统静默生成一份正文为 0 的档案。
  新增 `quality_flag()` 用中间灰占比识别该类图，命中后跳过去阴影、改用温和 CLAHE；
  增强产物写入 300 DPI（消除 ocrmypdf 的 `'dpi'` 警告），并通过 `X-Image-Quality`
  响应头让采集页在归档前提示「文字偏淡，OCR 可能识别不出，建议重拍」。
- **采集件题名是一串数字**：手机相册导出的纯数字文件名直接当题名，列表里无法辨认。
  前端 `readableTitle()` 与网关双重兜底改为「扫描件 + 成文日期」（按本地时间，不受容器 UTC 影响）。
- **移动端登录页右侧被裁**：`.login-wrap` 的 `1fr` 列最小尺寸为 `auto`，被固定 360px 卡片
  + 32px padding 撑成 424px，在 375px 视口下溢出被 `overflow-x: clip` 裁掉。
  改为 `minmax(0, …)` + `width:100%` + `100dvh`，补齐移动端品牌头与安全区，卡片左右对称。

### 验证

- 单元测试 31/31、端到端断言 24/24、浏览器回归 32/32（含登录页几何、浏览模式、
  无关查询、低对比度提示、双视口采集链路）。
- 公网真实浏览器实测：登录后检索页列出 9 份档案、1 份「未识别到文字」明确标注、无 JS 错误。
- 现存空文档（翻拍屏照片）题名已修正为可读的「扫描件 2026-11-05（未识别到文字）」。

## [2.1.1] - 2026-11-05

一次系统性交叉验证后的稳定性与体验修复：**登录态过期不再把用户锁死在「看起来正常、
实际全部 401」的界面**，同时完成采集页排版重做与移动端适配。

### 修复

- **「增强失败 HTTP 401」死局**：采集页与运维台此前用裸 `fetch` 绕过统一 `api()`
  客户端，401 不触发登出、不跳登录；错误解析又只读 `detail`，把网关结构化错误
  `{error:{message}}` 丢掉，只显示无意义的 `增强失败 HTTP 401`。叠加路由守卫只判断
  `localStorage` 里是否存在 token 字符串（不校验 `exp`），过期令牌会放行渲染页面，
  于是用户卡在「页面正常、操作全失败、无法自愈」。现在：
  - 新增 `authFetch()` 作为所有请求的唯一出口：三通道鉴权头 + `X-Request-ID`，
    401 统一登出、记住来源页、跳登录，重新登录后原路返回
  - `isTokenValid()` 解析 JWT `exp`，路由守卫据此拦截过期 / 畸形令牌
  - `readError()` 统一解析 `error.message` / `detail` / 纯文本，错误提示可读
  - 登录 cookie 的 `max-age` 改用后端 `expires_in`（12h），不再硬编码 8h
- **管理员菜单缺项**：`nav` 是 `computed` 却依赖非响应式的 `getUser()`，在登录页
  首次渲染即被缓存为基础 3 项，登录后「权限 / 审计 / 运维」不出现，必须刷新页面。
  改为显式依赖响应式 `user`，路由变化时按角色重算
- **增强失败阻塞归档**：单页增强失败会让可归档数为 0、批量归档按钮禁用，整个采集
  流程被单点失败卡住。现在失败页自动降级为「按原图归档」并明确标注，主流程始终可用；
  登录态失效则用专属 `AuthError` 跳过降级提示，避免误导
- **worker 启动竞态**：启动对账失败时插入 `doc_id=-1` 占位任务，它在依赖服务
  （Paperless / 向量模型）就绪前就被消费，重试至 `dead` 并刷 ERROR 日志。改为不插入
  占位任务，由首轮周期 `reconcile_missing()` 兜底，日志降级为 warning
- **E2E 脚本的数据副作用**：`e2e_test.py` 把抽检档案密级硬编码复位为 `0`（公开），
  每跑一次就悄悄降低一份档案的密级。改为先读取原密级与归属部门、验证后精确还原

### 新增

- `scripts/browser_regression.py`：Playwright 浏览器回归，覆盖桌面 / 375px 移动双视口、
  横向溢出断言、提示条边界、原图与增强预览渲染、过期令牌跳登录、401 自动登出与
  重新登录原路返回、管理员菜单按角色渲染

### 变更

- 采集页排版重做：卡片头 / 表单栅格 / 预览网格 / 提交条统一层级；长文件名优雅截断；
  提示条限定宽度并自动换行、可关闭，不再横向溢出遮挡内容
- 移动端适配：网格改用 `minmax(min(460px, 100%), 1fr)` 防窄屏溢出；顶栏 6 项菜单
  平铺可见；表单在手机上改为「标签 + 输入框」横向排布；补齐触控最小尺寸、
  iOS 16px 输入框防自动缩放、`viewport-fit=cover` 与刘海屏安全区

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
