## 变更说明

<!-- 用 1-3 句话说清「改了什么、为什么」；纯格式调整请直接标注。 -->

关联 Issue：<!-- #123，没有则写「无」 -->

## 变更类型

- [ ] `fix` 缺陷修复
- [ ] `feat` 新能力
- [ ] `refactor` 重构（不改变外部行为）
- [ ] `docs` 文档
- [ ] `test` 测试
- [ ] `chore` 构建 / 依赖 / CI

## 检查清单

- [ ] `python3 -m pytest tests/unit -q` 通过
- [ ] `python3 -m compileall -q gateway semantic shared` 通过
- [ ] `bash scripts/preflight-opensource.sh` 通过（不含密钥 / 私有链接 / 绝对路径）
- [ ] 前端改动已跑 `pnpm build`
- [ ] 涉及接口时同步更新 README / DEPLOY.md / 契约 `gateway/schemas.py`
- [ ] 涉及鉴权、密级或审计时补充了对应测试用例，且未削弱 fail-closed 边界
- [ ] 提交信息符合 Conventional Commits，并带 DCO 签名（`git commit -s`）
- [ ] 未提交 `.env`、`data/`、`hf-cache/`、`backups/`、`frontend/dist/` 等运行时产物

## 验证方式

<!-- 本地跑了哪些命令、端到端是否通过、有没有手工验证的界面/接口。 -->

## 其他说明

<!-- 破坏性变更、配置新增项、迁移步骤、截图等。 -->
