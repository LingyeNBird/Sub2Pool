# 后端架构

本文只说明额度计算相关模块的责任边界。部署、鉴权和页面结构见项目代码与 README。

## 数据流

```text
Sub2API 只读用量 ─┐
CPA usage 订阅 ───┼─> 采样证据 ─> 区间识别 ─> 账本重放 ─> 读取投影
上游整数百分比 ───┘                                  ├─> API/页面
                                                     └─> 人工余额建议（仅 Sub2API）
```

## 模块责任

- `monitor.integrations.sub2api`：访问 Sub2API；普通采样读取请求日志时严格校验 exact_total、分页游标、返回行数和重复 ID。
- `monitor.integrations.cpa` 与 `monitor.cpa`：读取 CPA Codex 账号和七天额度，并在同端口 RESP usage 订阅成功后，将活动 session 与每账号连接事件原子写入独立 SQLite spool；业务库以 `CPAAccountCollectionInterval` 独立保存连接覆盖，不从百分比观测推断连接状态。opening/closing 是可选整数百分比样本，人工排除不改变采集区间；正常结束由最终 RESP barrier 确认可靠截止点，异常退出按最后心跳恢复不可靠截止点且不伪造观测。只保存订阅成功后的直播事件，不读取 CPA FIFO。原子批量持久化不可删除的 Token/服务档位事实；延迟事件会刷新并重放其后的百分比观测，读取与重放时按当前本地价格计算成本，不保存原始 API Key。
- `monitor.sampling`：按 `UsageSamplePoint` 原子采集并保存来源事实，不决定参与者归属；普通 participant CRUD 不追溯改写历史。
- `monitor.historical_rebuild`：冻结本地全点审计计划，按 plan digest fail closed，并在 lease/fencing 保护下零联网重放派生结果。
- `monitor.history_state`：账号级 fact revision、lease 与 fencing token。
- `monitor.accounting`：识别归属区间，重放时变账本，保存兼容现有读取路径的 legacy live projection。
- `monitor.reporting`：把已有事实和账本投影为首页、统计和参与者数据，不改写账本。
- `monitor.views`：鉴权、校验和 HTTP 编排，不实现计算公式。
- Vue 前端：展示本地计划、blocker 和后端重放结论，不重复实现后端算法。

## 两类计算

### 描述性统计

“本周期累计折算”“今日用量折算”“累计收盘”“日内折算”只使用明确的起止观测和简单比值。它们不参与额度归属，见 [statistics.md](statistics.md)。

### 分配模型

“平均恒定”和“时变额度”负责参与者权益与余额建议。时变额度采用混合粒子滤波；平均恒定保持独立，见 [quota-models.md](quota-models.md)。

## 不变量

1. 上游百分比、观测时间、窗口边界和已采集余额不可被算法覆盖；无法证明恢复的事实保持 unknown。
2. 新采样的账号、完整用户集合、参与者趋势/余额及可选 observation/FAST 必须按一个 canonical point 原子提交。
3. 当前仍可查询的请求日志不能证明历史 retention 完整；缺失的历史 FAST 或请求数事实保持 unknown。
4. 维护 apply 必须只消费持久化 plan，零联网，并在同一事务内完成全点审计和 legacy projection 重放；不得改写来源成本。
5. 所有来源事实写入由 fact revision、lease 和 fencing token 协调；旧 owner 不得在租约失效后提交。
6. 算法不能调用 Sub2API 写接口；写余额只允许管理员显式操作。CPA 账号不创建参与者、余额、建议或人工额度调整。
7. 合同权益不参与消费事实推断，不得用当前参与者策略发明历史用户或 policy。
8. 未解释成本必须显式保留，不能静默分给已绑定参与者。
9. 相同原始数据、算法/构建版本和配置必须产生相同 legacy read projection。
10. CPA 百分比观测可以人工排除或恢复，但连接开关标记仍参与区间识别；`CPAUsageEvent` 是独立、不可排除的请求事实。

## 相关文档

- [数据与重放](data-and-replay.md)
- [统计口径](statistics.md)
- [额度模型](quota-models.md)
- [粒子滤波](particle-filter.md)
