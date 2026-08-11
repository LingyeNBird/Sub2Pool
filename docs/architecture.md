# 后端架构

本文只说明额度计算相关模块的责任边界。部署、鉴权和页面结构见项目代码与 README。

## 数据流

```text
Sub2API 只读用量 ─┐
                  ├─> 采样证据 ─> 区间识别 ─> 账本重放 ─> 读取投影
上游整数百分比 ───┘                                  ├─> API/页面
                                                     └─> 人工余额建议
```

## 模块责任

- `monitor.integrations.sub2api`：访问 Sub2API；严格分页扫描校验 exact_total 对应的实际行数，不把查询 horizon 或分页一致误报为历史 coverage。
- `monitor.sampling`：按 `UsageSamplePoint` 原子采集并保存来源事实，不决定参与者归属；普通 participant CRUD 不追溯改写历史。
- `monitor.historical_rebuild`：流式生成不可变 plan、逐维 coverage 与 typed patch journal；按 plan digest 校验 journal，负责零联网 apply 和业务 rollback。
- `monitor.history_state`：账号级 fact revision、lease 与 fencing token。
- `monitor.accounting`：识别归属区间，重放时变账本，保存兼容现有读取路径的 legacy live projection。
- `monitor.reporting`：把已有事实和账本投影为首页、统计和参与者数据，不改写账本。
- `monitor.views`：鉴权、校验和 HTTP 编排，不实现计算公式。
- Vue 前端：展示 plan、coverage、blocker 和后端结论，不重复实现后端算法。

## 两类计算

### 描述性统计

“本周期累计折算”“今日用量折算”“累计收盘”“日内折算”只使用明确的起止观测和简单比值。它们不参与额度归属，见 [statistics.md](statistics.md)。

### 分配模型

“平均恒定”和“时变额度”负责参与者权益与余额建议。时变额度采用混合粒子滤波；平均恒定保持独立，见 [quota-models.md](quota-models.md)。

## 不变量

1. 上游百分比、观测时间、窗口边界和已采集余额不可被算法覆盖；无法证明恢复的事实保持 unknown。
2. 新采样的账号、完整用户集合、参与者趋势/余额及可选 observation/FAST 必须按一个 canonical point 原子提交。
3. 查询 horizon、分页一致或返回非空日志均不构成历史 retention coverage；远端 patch 只接受逐维 `verified`/`verified_empty` 证据。
4. 维护 apply 必须只消费持久化 plan，零联网，并在同一事务内完成 typed patch、全点审计和 legacy projection 重放。
5. 所有来源事实写入由 fact revision、lease 和 fencing token 协调；旧 owner 不得在租约失效后提交。
6. 业务 rollback 只恢复 touched source before-image，并在 lease 内重验 LIFO、journal digest 与逐项恢复证明；它不承诺数据库字节恢复或数据库级 append-only。
7. 算法不能调用 Sub2API 写接口；写余额只允许管理员显式操作。
8. 合同权益不参与消费事实推断，不得用当前参与者策略发明历史用户或 policy。
9. 未解释成本必须显式保留，不能静默分给已绑定参与者。
10. 相同原始数据、算法/构建版本和配置必须产生相同 legacy read projection。

## 相关文档

- [数据与重放](data-and-replay.md)
- [统计口径](statistics.md)
- [额度模型](quota-models.md)
- [粒子滤波](particle-filter.md)
