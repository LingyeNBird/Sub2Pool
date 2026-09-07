# 开发与持续集成

## 本地检查

```sh
cd backend
uv run --frozen pytest
uv run --frozen python manage.py makemigrations --check --dry-run
uv run --frozen python manage.py check
cd ../frontend
pnpm install --frozen-lockfile
pnpm check
pnpm build
pnpm build:demo
```

浏览器回归使用临时数据库和合成账号，入口为 `scripts/billing_browser_smoke.py`、`scripts/research_browser_smoke.py`。不要在生产数据库上运行这些脚本。大型模拟实验按研究文档独立运行，不在每个 PR 重复执行全套研究；算法数值回归仍包含在后端测试中。

## 复用现有 CI，不按 PR 新建工作流

`.github/workflows/ghcr.yml` 是所有 PR 共用的 CI：一次后端全量测试、一次前端检查与双构建，再运行两组浏览器测试，最后验证 AMD64/ARM64 镜像。计费和科研测试是产品回归测试，不是一次性 review 流程。新增功能应在测试目录增加用例；需要新的测试入口时扩展已有 job/step 或矩阵，不生成 `review-pr-N.yml`。

`pages.yml` 是独立的演示站发布流程，职责不同，不因 PR 数量增加。镜像只在主分支或版本标签通过验证后发布；PR 仅构建。验证 job 只有仓库只读权限，不解包、恢复、生成或提交业务源码。测试产物只保留日志、报告和合成截图，不上传数据库或凭据。

工作流 YAML 是可反复运行的配置；每个 PR 产生的是运行记录，不是新的 YAML 文件。已删除的临时工作流可能仍显示在 Actions 历史记录中，那不是当前源码树中的配置。

参考：[GitHub CI 文档](https://docs.github.com/en/actions/get-started/continuous-integration)、[工作流复用](https://docs.github.com/en/actions/concepts/workflows-and-actions/reusing-workflow-configurations)。
