# 数据与重放

本文定义额度算法、采样和历史维护能够读取或改写的数据边界。核心原则是：**能查询到请求日志不等于日志覆盖完整，更不等于全部历史可恢复**。

## 事实分类

### 永久保留证据

以下数据不能由历史请求日志恢复，任何重放或维护计划均不得发明或覆盖：

- 观测时间、账号 ID、官方窗口时长和重置时间；
- 上游返回的整数已用百分比；
- 当时采集到的参与者余额；
- 管理员设置的人工起点、排除和恢复操作。

`ParticipantBalanceSample` 独立保存时间点余额证据。迁移自旧数据库的余额会保留来源标记；没有历史余额接口时，缺失值保持缺失。

### Canonical sampling point

每次新采样先建立一个 `UsageSamplePoint`，并在同一数据库事务中写入：

- 账号累计与相邻区间成本；
- 完整 Sub2API 用户集合及逐用户累计、区间成本；
- 参与者展示用趋势点和独立余额证据；
- 若本次读取百分比，再写 `Observation` 与 FAST 父级/明细事实。

只有整个事务成功后，point 才是 `write_status=complete`。它还保存 capture bounds、expected-user 数量与摘要、显式 residual 和 reconciliation 状态。任一步失败都会回滚整个事实组，不会留下“账号已写、用户只写一半”的采样点。

0024 以前的数据迁移为 `legacy_unknown`。迁移只建立 point、外键和余额证据，不联网、不修改历史金额，也不把旧行宣称为原子完整采样。

### 可验证远端事实

账号成本、逐用户成本、FAST/service-tier 成本和请求数在**特定半开区间**内可能由请求日志重新计算，但只有独立证据证明该维度完整覆盖时才允许覆盖本地事实。当前 Sub2API 的分页总数、`exact_total`、可查询天数或“返回了若干行”只证明本次分页一致，状态是 `policy_only`，不能证明更早日志未被清理。

API Key 构成没有独立历史覆盖证明时为 `unavailable`。系统不长期镜像请求日志；维护计划只持久化证据摘要、coverage 和需要应用的 typed before/after patch。

### 派生结果

归属区间、规范累计值、参与者权益与建议、概率范围、模型诊断及 legacy read projection 都可由保留事实和带版本算法重放。修改派生结果不得反向制造来源事实。

## 不可变维护计划

历史维护采用持久化 plan，而不是一个“mode 参数 + 立即执行”的请求。

1. `POST /api/settings/data-maintenance/history-rebuild-plans` 创建计划；
2. 计划冻结账号、cutoff、fact revision、源事实/配置/参与者策略摘要、算法版本、构建版本、过期时间、逐维 coverage、blocker 和 typed before/after patch；
3. `POST .../<plan-id>/apply` 必须提交相同 digest；
4. `POST .../<plan-id>/rollback` 只允许按 applied 栈逆序业务回滚。

计划或 patch journal 内容被修改、过期，或者源事实、fact revision、配置、参与者策略、算法/构建版本发生变化时，apply 与 rollback 都会按 plan digest fail closed。数据库本身不宣称提供 patch 表的 append-only 强制约束；不可变性由每次采用/回滚前重新计算 journal digest 验证。每个账号的单调 `fact_revision` 标识来源事实代次；采样、管理员余额事件、人工边界操作、参与者策略和相关设置变化都会推进 revision。

### 本地审计并重放

`audit_replay` 对所有 observation 与非 observation point 进行零联网审计，包括：

- 账号及逐用户窗口、相邻区间连续性与累计差值；
- expected-user 完整性；
- 账号累计/区间、用户累计/区间与显式 residual 对账；
- observation 与 point 的 natural key、成本及窗口坐标一致性；
- FAST 父级与逐用户明细、请求数一致性；
- orphan source rows。

存在 hard blocker 时计划不可应用；通过后 apply 只做确定性重放，不改写来源成本。

### 远端验证修复

`verified_remote_repair` 的查询 horizon 只是远端查询预算，不是 retention，也不是 coverage。目标范围外的健康点记录为 `out_of_scope` 诊断，不会因 coverage 阻断目标范围；真实全局 source invariant 仍会阻断。目标范围内按 point 和最多一天的日期块流式扫描，跨累计 point 复用相同块的聚合摘要，不持有或持久化整段请求日志。

- `account_cost`；
- `user_cost`；
- `fast_cost`；
- `request_count`；
- `api_key`。

coverage 状态包括 `verified`、`verified_empty`、`captured_local`、`out_of_scope`、`policy_only`、`unknown` 和 `unavailable`。远端 patch 只引用 `verified` 或 `verified_empty` coverage；验证为空是有效证据，可以把错误的非零事实修复为零。缺少 expected-user 集合、出现集合外用户或任一原子事实组所需维度未验证时，只保存 blocker，不生成可应用覆盖。

账号、逐用户和 FAST 是一个原子采用单元。系统不提供 unsafe force，也不再提供独立 FAST 历史重建端点；FAST 历史修复只能作为 `verified_remote_repair` plan 中受 coverage 约束的 typed patch。

## Apply 与并发

plan 创建阶段可以联网收集证据；apply 阶段禁止创建 Sub2API 客户端，只读取持久化 coverage 和 patch journal。apply 在一个数据库事务中完成：

1. 获取账号级可续期 lease 和单调 fencing token；
2. 锁定 plan、维护状态、设置及 touched source rows；
3. 验证 digest、TTL、revision、源事实/配置/策略摘要和每个 patch 的 coverage；
4. 按 schema_version 校验并应用 typed patch；
5. 审计 staged-after 全部 point；
6. 重建 legacy live projection；
7. 再次审计、验证 fence 并推进 fact revision。

监控采样使用同一 lease/fencing 协议。旧 owner 即使在租约过期后继续运行，也不能通过 token 检查提交。事务中任何 patch、审计或重放失败都会回滚整个事实组和派生结果。

参与者 create/update/delete、人工重放/排除/恢复/起点、影响重放的设置、运维重放命令和数据库导入也使用同一协议。数据库导入持有全局 lease，并把同一 owner 与更高 fencing token 写入待导入快照后再覆盖文件；因此替换 SQLite 的窗口不会擦除正在生效的 fence。

管理员一键余额调整在联网前持久化 `ParticipantBalanceOperation`。远端成功先独立记录为 `remote_confirmed`，再以原 revision 和 fence 原子提交余额证据；本地提交失败时重试不会再次写远端。网络结果不确定时操作保持 `reconciliation_required`，后续重试先读取远端余额，匹配目标则直接完成，否则幂等重设同一目标。未完成操作会阻断其他来源写入，避免远端成功但本地事件永久丢失。

## 业务回滚

rollback 不是数据库字节恢复。它只恢复该 plan touched source 的 typed before-image，然后用同一算法和配置重放 legacy projection：

- 只能回滚账号最近一次 applied plan；
- 当前 touched source 必须仍等于 plan 的 after-image；
- plan digest 必须重新证明 coverage 与 typed patch journal 未被修改或追加；
- 每条 touched source 恢复后必须逐项等于 typed before-image，新增行必须证明确已删除；
- 算法、构建、配置和参与者策略必须仍满足计划承诺；
- cutoff 后新增且未触碰的采样及后续管理员余额事件保留；
- 没有后续来源写入时，source 和 API 可观察 hash 必须恢复到 before hash。

回滚成功同样推进 fact revision，因此旧计划不会重新变为可应用。

## 归属区间

区间边界按以下优先级确定：

1. 管理员明确设置的人工起点；
2. 官方 `reset_at` 变化形成的新窗口；
3. 当前官方窗口的自然起点。

同一 `reset_at` 内百分比明显回退与官方窗口证据冲突，应先排除该观测，等待窗口更新或管理员处理。连续 0% 只表示尚未消费，不足以证明产生多个周期：原始采样全部保留，派生账本只使用最后一个 0% 候选基线。

算法输入统一归一化为区间起点的 0 美元、0% 和 0 小时。没有真实 0% 观测时，可根据已确认的官方或人工边界构造计算用零点；该零点是派生输入，不写成伪造观测。

## 成本坐标与未解释成本

Sub2API 累计统计按自然日期查询；查询起点变化时累计值不能直接相减。新采样同时保存累计快照、实际查询窗口和相邻增量。每个区间以已保存的 Sub2API 用户并集构建成本轨迹；尚未绑定的用户和账号总成本与用户合计的正 residual 进入“未解释主体”，参与进度推断但没有合同权益，也不生成余额建议。

用户合计高于账号总成本时不缩放用户原始轨迹，而是记录冲突并阻断不安全维护。系统不会用当前参与者列表倒推出历史 expected-user，也不会为过去制造 participant policy；普通参与者 create/update 只影响当前及未来策略，不补建或改写过去的 membership、share、usage 或 snapshot。

## 确定性重放

新增末尾观测从当前区间起点重放；历史插入、人工起点、排除、恢复或算法升级从最早受影响边界向后重放。维护 apply/rollback 在来源事实事务内重放全部受影响结果。

粒子滤波随机种子必须来自稳定事实，例如：

```text
算法版本 + 账号 ID + 区间起点
```

相同来源事实、算法版本、构建版本和配置必须产生相同 legacy read projection。重放不发送通知，也不调用 Sub2API 写接口；写余额仍只允许管理员显式操作。
