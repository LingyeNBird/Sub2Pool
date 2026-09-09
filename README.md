<div align="center">
  <img src="frontend/public/favicon.png" alt="Sub2Pool" width="104" />
  <h1>Sub2Pool</h1>
  <p>面向 Sub2API 拼车账号与 CLIProxyAPI（CPA）Codex 账号的周限测算、用量统计和监控服务。</p>
</div>

## 功能

- **多平台账号监控**：支持 Sub2API 拼车账号与 CLIProxyAPI（CPA）Codex 账号，查看周额度和用量变化。
- **请求用量与费用统计**：查看 CPA 请求用量、费用和各 API Key 的消耗，支持自定义模型价格，并提示未计价请求。
- **额度池与份额管理**：支持单账号独立分配或多账号合池，按参与者份额分配额度。
- **周额度测算**：提供周总额度估算、变化趋势和估算范围，辅助判断账号可用额度。
- **余额建议与应用**：根据参与者份额和用量生成余额建议，支持一键设置或自动应用到 Sub2API；自动应用默认开启，可在设置中关闭。
- **临时爽蹬**：车主选择结转或不结转，临时放开余额并加速采样；不结转需告知所有车友，结转无需逐一通知。支持提前终止、恢复普通建议并取消本轮后续结转，提供[图示教程与示例](docs/temporary-burst.md)。
- **上游计费管理**：按 Sub2API 分组调整 FAST 倍率、模型价格和长上下文计费，支持查看当前配置、失败重试和撤回修改。
- **观测记录管理**：查看历史观测，支持排除异常观测、恢复记录和重新测算。
- **可视化看板**：提供额度总览、账号状态、测算轨迹和用量统计。
- **邮件通知**：在余额耗尽、建议余额变化、测算率变化或采集异常时发送提醒，支持 SMTP 和 Resend。
- **用户与访问权限**：为普通用户配置可查看的页面、账号和参与者，管理操作仅限管理员。
- **API 接入**：提供业务 API、在线接口文档和个人 API Key，支持查询数据；管理员可通过 API 应用建议余额。
- **登录审计**：查看登录记录和来源 IP。
- **数据备份与迁移**：支持完整导入、导出数据库，便于备份和迁移服务器。
- **内置使用教程**：提供配置与使用指引。

> 使用共享订阅或相关网关前，请自行确认上游服务条款及所在地法律要求。

## 从 GHCR 部署

公开镜像地址：

```text
ghcr.io/lingyenbird/sub2pool:latest
```

以下方式只下载 Compose 文件和环境变量样例，不需要克隆仓库。

### 1. 下载部署文件

```bash
mkdir -p sub2pool
cd sub2pool
curl -fsSL https://raw.githubusercontent.com/LingyeNBird/Sub2Pool/main/compose.ghcr.yaml -o compose.yaml
curl -fsSL https://raw.githubusercontent.com/LingyeNBird/Sub2Pool/main/.env.example -o .env
```

### 2. 配置环境变量

先生成随机的 Django Secret Key：

```bash
openssl rand -hex 32
```

编辑 `.env`，至少替换以下内容：

```dotenv
DJANGO_SECRET_KEY=粘贴刚生成的随机值
ADMIN_USERNAME=admin
ADMIN_PASSWORD=设置一个足够强的初始密码
DJANGO_ALLOWED_HOSTS=你的域名,服务器IP
WEB_PORT=8088
```

如果通过 HTTPS 反向代理访问，还应设置：

```dotenv
DJANGO_CSRF_TRUSTED_ORIGINS=https://你的域名
COOKIE_SECURE=true
TRUSTED_PROXY_COUNT=1
```

`ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 只在数据库中不存在该管理员时用于首次创建。后续密码修改请使用系统设置页面。

### 3. 启动

```bash
docker compose pull
docker compose up -d
docker compose ps
```

浏览器访问 `http://服务器IP:8088`。首次登录后，在“系统设置”中配置 Sub2API 地址与 Admin Token，或配置 CPA 地址与 Management Key，再读取并添加需要监控的账号。CPA 连接测试同时验证 Management API、同端口 RESP 传输与鉴权，设置页每 5 秒独立刷新采集器心跳、待写数量和最近错误，不会重载或覆盖未保存的表单。监控账号创建后只允许停用或重新启用，不提供硬删除；停用 CPA 账号只停止额度采样和页面监控，已纳管账号的原始 usage 事实仍会继续采集。

### 更新镜像

```bash
cd sub2pool
docker compose pull
docker compose up -d
```

SQLite 数据保存在 Docker 命名卷 `sub2pool_sub2pool-data` 中，更新或重建容器不会删除数据。迁移服务器前，建议同时使用系统设置中的“数据库迁移”功能导出完整备份。

### 查看日志和停止服务

```bash
docker compose logs -f app
docker compose down
```

不要使用 `docker compose down -v`，除非确定要删除 SQLite 数据卷。

## 从源码构建

```bash
cp .env.example .env
# 编辑 .env 后启动
docker compose up -d --build
```

本地 Compose 仍使用单容器架构：构建阶段编译 Vue 3，运行阶段由 Django/WhiteNoise 提供前端静态文件和 SPA 路由。

## 算法与开发文档

- [后端架构](docs/architecture.md)
- [数据与重放](docs/data-and-replay.md)
- [计费修正与升级说明](docs/billing-corrections.md)
- [临时爽蹬：操作、图示与跨周期结算](docs/temporary-burst.md)
- [统计口径](docs/statistics.md)
- [额度模型](docs/quota-models.md)
- [时变额度粒子滤波](docs/particle-filter.md)

## 在线演示

GitHub Pages 提供不连接后端的公开演示：

- 地址：<https://lingyenbird.github.io/Sub2Pool/>
- 账号：`admin`
- 密码：`123456`

演示中的参与者、观测、统计、粒子轨迹、通知和登录记录均为确定性合成数据；所有写操作只影响当前浏览器标签页，不连接 Sub2API、CPA、数据库或邮件服务。

## CI 与镜像标签

GitHub Actions 工作流采用“自动发布为主、手动触发兜底”的方式：

- Pull Request：运行后端测试、前端检查和多架构 Docker 构建，但不推送镜像。
- 推送到 `main`：验证通过后自动发布 `latest`、上海时区的 `YYYYMMDD-HHmm` 时间标签和 `sha-<commit>`。
- 推送 `v*` Git 标签：发布语义化版本标签，例如 `1.2.0`、`1.2` 和 `latest`。
- `workflow_dispatch`：可在 GitHub Actions 页面手动重新构建和发布。

例如 2026 年 8 月 6 日 19:05 发布的镜像会同时获得：

```text
latest
20260806-1905
sha-51984cb
```

日常回退可以直接把 Compose 中的镜像改成时间标签；若同一分钟内连续发布，则使用不会冲突的 `sha-<commit>` 精确定位：

```yaml
image: ghcr.io/lingyenbird/sub2pool:20260806-1905
```

镜像同时支持 `linux/amd64` 和 `linux/arm64`，并附带 SBOM 与构建来源证明。

## 开发检查

后端：

```bash
cd backend
uv run pytest
```

前端：

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm check
pnpm build
```

图标处理脚本仅在开发阶段使用 Pillow，不会增加运行镜像依赖：

```bash
uv run --with pillow python scripts/prepare_icon.py 原图.png frontend/public/favicon.png \
  --apple-touch-output frontend/public/apple-touch-icon.png
```

该脚本只清除与画布边缘连通的近白色背景，因此会保留图标内部的白色标志，并自动裁剪到实际像素边界。

## 安全说明

- `.env`、SQLite 数据库、虚拟环境、构建产物和本地 `reference/` 均已从 Git 排除。
- Admin Token、CPA Management Key、SMTP 密码和 Resend Key 加密存储在 SQLite 中；CPA API Key 在进入磁盘持久队列前即替换为本地 HMAC 摘要与末尾四位提示，不保存原值。主数据库和 `cpa-usage-spool.sqlite3` 都位于数据卷中并应作为敏感文件保管；“导出完整数据库”只导出已写入主数据库的事实，不包含仍在 spool 中的待写记录。
- WebRTC 地址只作为浏览器自报的辅助线索，服务端观测到的请求来源地址才是登录审计的主要依据。
- 建议部署在 HTTPS 反向代理之后，并限制管理页面的网络访问范围。

## 许可证

本项目以 [GNU Affero General Public License v3.0 only](LICENSE) 发布。

运行中的 Web 界面在账户菜单中提供本仓库源码链接，以满足 AGPL 网络交互场景下的源码获取要求。

## 前端来源

本项目的前端基于 daisyUI 的 [HTML Dashboard Template](https://daisyui.com/store/html-dashboard) 开发。

## 友链

- [LINUX DO](https://linux.do/)

## 自愿科研共创（默认关闭）

当前为**原始证据联合共研v2**。管理员在系统设置中选择GPT-6额度异常归因并明确授权，默认接收网站为 `https://codex.nightunderfly.online`。先部署配套[统计平台](https://github.com/LingyeNBird/CodexSubscribeStudy)，再升级客户端。新增0051迁移只替换旧占位地址、保留自定义地址，清除旧授权但不删除身份、历史贡献或原始事实。

无请求数、周期数或本地置信度门槛，一条请求也可以贡献；中央共同推断，客户端不先投票。固定FAST目标2、GPT-5.6/GPT-6长上下文目标1，其他模型既有价格作为控制，混合请求可以参与。**粒子滤波和平均恒定容量估值不上传、不读取、不用于辅助分析。** 正常额度监控和当前运行倍率不被研究修改。

定时任务使用本地已捕获的原始事实，不额外请求模型或从上游回抓。按匿名批次更新，重复不加量；不同批次持续保留，无120天自动清理。关闭只停止发送，已经提交的贡献由接收网站长期保留。缺少额度或成本证据时保留请求统计并说明缺失，不编造归因。

只上传汇总计数、候选证据曲线、信息矩阵和候选归因合计，不上传请求明细、账号/参与者、Token明细、API Key、时间线或IP字段。随机公钥可关联更新，不是绝对匿名；单条贡献无k匿名保证，网络层仍可见出口IP。

Docker入口运行独立 `runresearch` 进程。非Docker须另外托管 `python manage.py runresearch` 或定期 `--once`。方法和原始数据边界见 [原始证据共研说明](docs/research.md)。
