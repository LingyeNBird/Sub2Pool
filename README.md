<div align="center">
  <img src="frontend/public/favicon.png" alt="Sub2Pool" width="104" />
  <h1>Sub2Pool</h1>
  <p>面向 Sub2API 的拼车周限测算、人工额度建议与通知服务。</p>
</div>

## 功能

- 通过 Sub2API Admin Token 被动读取已保存的 OpenAI 周限快照，默认不主动请求上游额度接口。
- 按参与者合同权益维护百分比账本，并根据真实消费增量估算每 1% 周限对应的美元价值。
- 在参与者用户余额耗尽、建议余额变化、测算率变化或采集异常时发送邮件通知。
- 支持 SMTP 和 Resend 邮件服务。
- 提供额度总览、参与者管理、观测记录、额度统计、登录审计和使用教程。
- 管理员可创建普通系统用户并绑定一个或多个参与者；普通用户只能查看额度统计及其绑定参与者的用量。
- 记录服务端来源 IP，并可选记录浏览器通过 WebRTC 上报的辅助地址。
- 支持数据库完整导入、导出，便于服务器迁移。
- Django、Vue 3 和 SQLite 打包在一个容器中，不依赖 Redis 或单独的前端容器。

> 本项目只生成手动调整建议，不会自动修改参与者额度。使用共享订阅或相关网关前，请自行确认上游服务条款及所在地法律要求。

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

浏览器访问 `http://服务器IP:8088`。首次登录后，在“系统设置”中填写 Sub2API 地址和 Admin Token，再读取 OpenAI 上游账号及 Sub2API 用户。

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
- Admin Token、SMTP 密码和 Resend Key 加密存储在 SQLite 中；导出的完整数据库仍应作为敏感文件保管。
- WebRTC 地址只作为浏览器自报的辅助线索，服务端观测到的请求来源地址才是登录审计的主要依据。
- 建议部署在 HTTPS 反向代理之后，并限制管理页面的网络访问范围。

## 许可证

本项目以 [GNU Affero General Public License v3.0 only](LICENSE) 发布。

运行中的 Web 界面在账户菜单中提供本仓库源码链接，以满足 AGPL 网络交互场景下的源码获取要求。

## 前端来源

本项目的前端基于 daisyUI 的 [HTML Dashboard Template](https://daisyui.com/store/html-dashboard) 开发。
