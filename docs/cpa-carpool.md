# CPA 拼车

CPA 复用 Sub2Pool 的系统用户、参与者和额度池。一个参与者可以只绑定 CPA Key，也可以同时绑定一个 Sub2API 用户；一个人可持有多个 CPA Key。两个渠道的额度池、权益和余额分别计算。

## 配置流程

1. 在系统设置中配置 CPA Management API 和 usage 订阅连接，添加监控账号。
2. 在参与者页面添加成员；只用 CPA 时，Sub2API 用户选择“仅使用 CPA”。
3. 切换到 CPA，选择成员后绑定已采集的 Key，或输入完整 Key 预先绑定；可以填写、修改 Key 备注。
4. 在额度分配中切换到 CPA，设置独立池或多个 CPA 账号组成的混池，以及各成员份额。池内份额合计不能超过 100%，不得跨渠道混池。
5. 在系统用户页面创建登录账号，授权对应参与者、CPA 账号及总览／参与者／统计等页面。普通用户同时满足账号授权和池成员关系才能查看该池。

绑定从提交时刻生效；解除绑定只关闭归属时间区间，过去的请求仍归原成员。原始 Key 仅用于计算现有 HMAC 摘要，不写入数据库、spool 或返回值。末四位相同的 Key 仍由完整摘要区分。

## 历史请求认领

绑定后可打开“历史认领”，选择参与者、开始时间和结束时间，先预览请求数、Token、费用、未计价请求和采集缺口，再确认认领。范围采用 `[开始, 结束)`；默认结束时间精确衔接当前绑定的生效时间。

预览有效期为 15 分钟，冻结请求事实、Key 归属、价格配置、观测和额度池合同。确认时若这些数据变化，需要重新预览。重复确认已完成的预览不会重复归属。时间范围不能与常规绑定或其他参与者的认领重叠；同一参与者可在原范围再次预览，只认领新收到且尚未归属的请求。

认领仅新增归属记录和确认时的事件清单并重算派生额度，不修改 `CPAUsageEvent`。确认后迟到的历史请求不会自动加入认领，需再次预览确认。断线时未采集的请求不会补造。当前份额不会被追溯到历史；历史合同缺失时可以查看请求与消耗，历史剩余权益保持未知。有 Key 归属、认领计划或 CPA 合同历史的参与者只能停用。

## 展示口径

- 总览和参与者页面显示 CPA 池成员的份额、请求数、Token、请求估算费用，以及可计算时的权益总额、已用权益、剩余权益和超额状态。
- 个人 Key 的费用轨迹接入现有平均恒定／时变模型；未绑定 Key 的消耗保留在未归属主体中，不分摊给成员。成员在 CPA 的重放不会改动其 Sub2API 全局余额。
- 多账号池分别依据各账号的上游重置周期计算，再汇总等效美元权益。仅授权部分账号时明确标注部分范围，不暴露其他账号用量。
- 请求费用按当前本地模型价格重算，不代表 CPA 可扣减余额。未知模型保留请求数和 Token，同时标明未计价；它及采集缺口、有效观测不足会阻止展示可靠剩余额度。
- 请求更新时间和额度观测时间分别显示。usage 尚在 spool 或采集器未连接时会提示数据延迟。
- 普通用户能看同车成员汇总，只能看本人授权参与者的 Key 分项和逐次请求。管理员可看全部。逐次请求包含模型、Token、费用、成功／失败、请求标识、耗时和首 Token 延迟，不包含对话正文。
- 当前仅展示额度和超额提示，不会停用 Key 或拦截调用。

## API

所有路径以 `/api` 为前缀。管理接口使用管理员 JWT；只读接口还提供 `/v1/cpa/summary` 和 `/v1/cpa/requests`，使用 Sub2Pool 个人或管理员 API Key，执行相同的数据范围限制。CPA 调用 Key 不作为面板登录凭据。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /cpa/keys` | 管理员读取已登记 Key、绑定区间和未登记的已采集 Key |
| `POST /cpa/keys` | 提交 `participant_id`、可选 `name`，以及二选一的 `raw_key`／`observed_hash` |
| `PATCH /cpa/bindings/{id}` | 修改 Key 的 `name` 备注 |
| `DELETE /cpa/bindings/{id}` | 幂等结束当前绑定，保留历史 |
| `POST /cpa/claims/preview` | 提交 `key_id`、`participant_id`、`started_at`、`ended_at`，返回预览和计划 ID |
| `POST /cpa/claims/{id}/apply` | 确认认领预览，重算受影响账号 |
| `GET /cpa/summary?account_id=...` | 返回该账号所属池在调用者授权范围内的成员汇总 |
| `GET /cpa/requests?account_id=...` | 查询本人／管理员可查看的请求 |

请求查询支持 `participant_id`、`key_id`、`model`、`failed`、`started_at`、`ended_at`、`page`、`page_size`。默认最近 7 天，最多 90 天；默认每页 50 条，最多 100 条，按请求时间和 ID 倒序。返回的 Key 和模型选项也按调用者权限过滤。

`GET /quota-allocation?provider=cpa` 读取 CPA 分配；`PUT /quota-allocation` 的请求体增加 `provider: "cpa"`。省略渠道时仍处理 Sub2API，保存一个渠道不会删除另一个渠道的分配。参与者的 `sub2api_user_id` 现在允许为 `null`。

## 升级与后续

迁移保留参与者 ID、已有授权和所有原始 CPA 请求，不自动认领历史。现有 Sub2API 参与者无需重新配置。第一次使用 CPA 拼车时按上述流程添加绑定与份额。

### 部署当前 CPA 分支

本次功能位于 `https://github.com/ld0574/Sub2Pool` 的 `cpa` 分支。该分支的推送不会自动发布镜像；README 中的上游 `ghcr.io/lingyenbird/sub2pool:latest` 不保证包含这次改造。当前可从源码构建。

全新安装：

```bash
git clone --branch cpa https://github.com/ld0574/Sub2Pool.git sub2pool
cd sub2pool
cp .env.example .env
# 编辑 .env：设置随机 DJANGO_SECRET_KEY、管理员密码和域名/IP。
docker compose up -d --build
docker compose logs --tail=100 app
```

已有源码部署：在原部署目录中更新到本仓库的 `cpa` 分支，再执行 `docker compose up -d --build`。保留原 `.env`、Compose 项目名和数据卷挂载；不要重新生成已有实例的 `DJANGO_SECRET_KEY`，加密配置和 CPA Key 摘要依赖它。

已有 GHCR 部署：先在另一个目录构建本地镜像，然后在原部署目录增加覆盖文件，以沿用原实例的数据卷和环境变量：

```bash
git clone --branch cpa https://github.com/ld0574/Sub2Pool.git sub2pool-cpa-src
docker build -t sub2pool-cpa:local ./sub2pool-cpa-src

# 以下命令在原来存放 compose.yaml 和 .env 的部署目录执行。
cat > compose.cpa.yaml <<'YAML'
services:
  app:
    image: sub2pool-cpa:local
    pull_policy: never
YAML

# 停止写入后备份整个数据目录，包含主数据库和 CPA spool。
backup_dir="./backup-cpa-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
docker compose stop app
docker compose cp app:/app/data/. "$backup_dir/"
docker compose -f compose.yaml -f compose.cpa.yaml up -d --no-build
docker compose -f compose.yaml -f compose.cpa.yaml logs --tail=100 app
```

源码升级也应在重建前使用上述停止与备份命令。若原部署使用额外的 `-p`、`--env-file` 或其他 Compose 文件，升级命令继续使用相同参数。后续使用本地镜像部署时，继续带上 `-f compose.cpa.yaml`。

容器启动脚本会自动执行 `migrate`（包括 `0052`、`0053`）、历史重放及管理员初始化，无需手工进入容器迁移。首次升级的历史重放可能延长启动时间，可通过日志确认完成，再访问 `http://服务器IP:8088`（或原访问地址）。升级后按本文配置流程设置 CPA 成员、Key、份额和系统用户授权。

第二阶段再验证目标 CPA 版本的 Key 控制能力，选择直接控制或请求网关，实现超额自动限制。
