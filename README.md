<div align="center">
  <img src="frontend/public/favicon.png" alt="Sub2Pool" width="104" />
  <h1>Sub2Pool</h1>
  <p>面向 Sub2API 拼车账号与 CLIProxyAPI（CPA）Codex 账号的周限测算、用量统计和监控服务。</p>
</div>

## 功能

- 通过 Sub2API Admin Token 被动读取已保存的 OpenAI 周限快照，默认不主动请求上游额度接口。
- 通过 CLIProxyAPI Management API 读取 Codex 七天额度，并在同一 CPA 端口使用内置 RESP `SUBSCRIBE usage` 持续接收事件。RESP 订阅成功后，活动 session 与每个账号的 `connected` 事件在独立 SQLite spool 中同事务落盘，再持久化为与百分比观测无关的账号采集区间；结束事件独立关闭区间，正常结束的 RESP `PING` barrier 确认此前 usage 已进入持久队列，异常退出则按最后心跳记录不可靠结束时间。opening/closing 只是在订阅后与关闭前读取的可选整数百分比样本，不决定连接状态；人工排除它们不会改写采集区间，也不会删除 `CPAUsageEvent`。连接前或断线期间的百分比观测只保留审计事实并自动排除，未知缺口不连续计算。采集器不读取或消费 CPA FIFO，也不回补缺失请求；业务数据库繁忙时持续重试，订阅读取线程定期发送 RESP `PING` 检测半开连接，原始 API Key 在进入持久队列前移除，容器停止时 PID 1 转发 `SIGTERM`，采集器发送 `UNSUBSCRIBE` 并排空确认帧之前的事件，不依赖外部 Redis。
- CPA usage Token 事实不可排除或删除；管理员只能排除或恢复百分比观测。spool 中延迟写入的 usage 会原子刷新并重放其后已有的百分比观测，避免永久保留过时成本快照。
- CPA 请求成本在读取和历史重放时按当前本地模型价格动态计算；未知模型按 0 美元计入并保留未计价请求数，不阻断采样、统计或价格保存。人工价格是数据库中的权威值，升级不会覆盖；只有 `fast`/`priority` 请求应用独立 FAST 倍率，长上下文双倍计费默认关闭。
- 按额度池维护参与者合同权益：单账号默认是独立池，也可在二维分配表中多选、右键合并为混池；各池独立配置百分比并汇总为参与者全局余额建议。
- 上游整数百分比、观测时间、周期边界和已采集余额永久保留；新采样按 canonical point 原子提交，历史维护先冻结不可变的本地审计计划，再零联网应用并确定性重放派生结果。
- 在一个“计费修正”卡片内管理 FAST、长上下文和模型倍率的有序通配规则。默认 FAST 为 2→2.5，GPT-5.6 / GPT-6 长上下文为 2→1，GPT-6 模型倍率为 1.8。新增观测保留原始请求成本、模型、档位与上下文计费事实，修改规则后在本地重放；观测、账号状态和结论依据统一显示可展开的“修正合计”。升级前只有 FAST 汇总的旧区间保留原值并标记证据不足，不推测缺失请求。详见 [计费修正与升级说明](docs/billing-corrections.md)。
- 平均恒定模型保留透明的累计比例对照；时变额度模型使用混合粒子滤波估计连续容量、整数显示规则和参与者归属，并同时给出概率区间与确定性边界。
- 在参与者用户余额耗尽、建议余额变化、测算率变化或采集异常时发送邮件通知。
- 支持 SMTP 和 Resend 邮件服务。
- 提供额度总览、账号状态、观测记录、粒子轨迹、额度统计、登录审计和使用教程；CPA 统计按本地哈希后的 API Key 分组，并显示未知模型的未计价请求数，不创建参与者、余额或额度建议。
- 管理员可为普通系统用户逐页配置只读访问权限，并精确选择其可见账号与参与者；账号范围同时约束额度总览、账号状态、观测、粒子轨迹和统计，所有页面写操作仍仅限管理员。系统设置页无需授权且不可关闭，普通用户在其中只会看到自己的 API Key 卡片。
- 提供内置 OpenAPI 3.1 文档的业务 API；管理员全局 Bearer Key 拥有全部已开放权限并可一键设置建议余额，普通系统用户可在系统设置页生成绑定自身的 Key，其端点发现、OpenAPI 文档和业务响应实时继承可配置页面、账号与参与者权限，且不能执行管理写操作。
- 记录服务端来源 IP，并可选记录浏览器通过 WebRTC 上报的辅助地址。
- 支持数据库完整导入、导出，便于服务器迁移。
- Django、Vue 3 和 SQLite 打包在一个容器中，不依赖 Redis 或单独的前端容器。

> 后台监控不会自动修改参与者额度；只有管理员显式点击“一键设置”才会调用写接口。使用共享订阅或相关网关前，请自行确认上游服务条款及所在地法律要求。

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
