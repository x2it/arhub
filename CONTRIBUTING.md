# 贡献指南

感谢你愿意为 档枢 ArcHub 做贡献。本项目采用 **Apache-2.0** 许可，并沿用
[Developer Certificate of Origin (DCO)](https://developercertificate.org/) 约定：
提交即表示你确认有权以本项目许可证贡献该代码。

## 一、开始之前

- 先在 Issue 里说明你打算解决的问题或新增的能力，避免重复劳动；大改动建议先开讨论。
- 安全漏洞**不要**开公开 Issue，请按 [SECURITY.md](SECURITY.md) 私下报告。
- 本项目面向**单机内网私有化**，任何 PR 都不应削弱越权过滤、审计、密级 fail-closed 等安全边界。

## 二、本地环境

```bash
git clone https://github.com/x2it/arhub.git   # Fork 后换成你自己的仓库地址
cd arhub
bash scripts/up.sh                 # 起 6 容器并通过 /ready 门禁
python3 -m pytest tests/unit -q    # 单元测试
python3 scripts/e2e_test.py        # 端到端断言（需先起栈）
bash scripts/preflight-opensource.sh   # 开源合规自检
```

前端单独开发：

```bash
cd frontend
pnpm install
pnpm dev                           # 需将 Vite 代理指向本机 8700，见 vite.config.ts
pnpm build                         # vue-tsc + vite，产物 dist/ 由网关同源托管
```

改了后端代码需重建镜像：`bash scripts/up.sh --rebuild`。

## 三、代码约定

| 层面 | 约定 |
|---|---|
| Python | 3.11，类型标注优先；配置一律走 `shared/settings.py`，禁止散落硬编码；日志用 `shared/logging_conf.py` 的结构化 JSON |
| SQL | 迁移只增不改（`migrations/00x_*.sql`），必须幂等（`IF NOT EXISTS` / `ON CONFLICT`）；initdb 阶段不得引用 Paperless 尚未创建的表 |
| API | 统一契约在 `gateway/schemas.py`；所有端点必须带鉴权与审计；响应体带 `request_id` |
| 安全 | 新增检索路径必须过 `post_filter_hits` 密级过滤；缺失密级记录一律拒绝 |
| 前端 | Vue3 `<script setup lang="ts">`；用户可控 HTML 必须走白名单 sanitize |
| Shell | `set -euo pipefail`，`bash -n` 通过；不写死绝对路径与本机密钥 |
| 提交 | [Conventional Commits](https://www.conventionalcommits.org/)：`feat: ` / `fix: ` / `docs: ` / `chore: ` / `test: ` / `refactor: `，并加 DCO 签名 `git commit -s` |

## 四、PR 检查清单

- [ ] `python3 -m pytest tests/unit -q` 通过
- [ ] `python3 -m compileall -q gateway semantic shared` 通过
- [ ] `bash scripts/preflight-opensource.sh` 通过（不含密钥、私有链接、绝对路径）
- [ ] 前端改动：`pnpm build` 通过
- [ ] 涉及接口：同步更新 `/api/docs` 相关说明与 README / DEPLOY.md
- [ ] 涉及安全边界：补充或更新对应测试用例
- [ ] 提交信息符合 Conventional Commits，且带 `Signed-off-by`
- [ ] 不提交 `.env`、`data/`、`hf-cache/`、`backups/`、`frontend/dist/` 等运行时产物

## 五、行为准则

所有参与者须遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。维护者有权删除违反准则的
评论、Issue 与 PR，情节严重者可临时或永久封禁。

## 六、路线图方向（欢迎认领）

- 多租户隔离（`TENANT_ID` 已有字段，尚未全链路贯通）
- 增量 OCR 结果的质量评估与人工校正界面
- 更多语种嵌入模型与 OCR 语言包的可插拔配置
- 归档格式长期保存（PDF/A 校验、校验和巡检）
- 可选的 S3 兼容对象存储后端
