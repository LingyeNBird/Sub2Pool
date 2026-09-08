# CPA 账号状态与重置设计验收

final result: passed

## 设计目标与证据

以用户提供的 CPA Manager Plus 两张截图为信息层级参考，在现有账号状态菜单中实现 CPA 独立卡片。沿用 Sub2Pool 的导航、系统字体、daisyUI 主题和 Heroicons，不引入参考产品的抽屉、禁用凭证或配置菜单。

- Source visual truth：`/var/folders/s5/fcvcqjmn2dn4jjr2m4qqq79r0000gn/T/codex-clipboard-8a8ce6a5-b366-43ee-8e2c-055e6f856da9.png`（1312 × 1404 px）及 `codex-clipboard-68187fe7-1c5b-4465-b482-92cade5a55be.png`（1258 × 720 px）。来源截图的 CSS viewport / DPR 未知。
- Implementation：`http://127.0.0.1:5180/#/account-status`，本地合成数据管理员；周限额剩余 89%，Spark 剩余 100%，初始重置次数 1。
- 桌面 CSS viewport / 图片：1536 × 1100，DPR 1；`/tmp/sub2pool-status-design/desktop.png`、`desktop-bottom.png`。
- 手机 CSS viewport / 图片：390 × 844，DPR 1；`mobile.png`、`mobile-windows.png`、`confirm-mobile.png`、`reset-zero-mobile.png`，均在同一验收目录。
- 确认与重置结果：`confirm-desktop.png`、`reset-zero-desktop.png`；只操作本地演示数据，未请求线上重置。
- 并排比较：`comparison.png` 对齐总体统计与标准窗口，`comparison-bottom.png` 对齐附加限额与零次数重置区域；两侧按内容宽度等比归一到 760 px，无拉伸。排除来源抽屉和实现导航的差异。
- 两个局部并排比较覆盖本次全部 CPA 内容区域，数字、对齐、说明和重置控件均可辨认；原始桌面截图用于查看完整页面关系。

## 视觉检查与修正

1. [P2，已修复] 剩余比例与标题挤在左侧：修正弹性布局下的数值对齐，最终桌面图显示比例位于右侧，手机允许换行。
2. [P2，已修复] 浅色主题的语义色文字偏浅：剩余数值、缺价和范围提示改用正文色，保留图标与进度条的语义色。最终截图和并排比较确认关键数字及说明可读。
3. 字体：沿用系统字体，标题与数字加重，数值等宽；主体 14–16 px，说明 12 px，关键指标 24–30 px。参考图片的字体尺寸与密度不直接推定为 CSS 大小。
4. 布局：四个总体指标；标准窗口三列用量卡；附加限额和重置独立成卡。手机指标两列、窗口单列，确认框完整显示，页面 scrollWidth 与 innerWidth 均为 390。
5. 颜色：使用现有主题的背景、边框和状态色，绿色进度表示剩余比例；图标只作辅助，状态和空值都有文字。保留原产品主题与参考蓝白配色的差异。
6. 资产：参考只有标准 UI 图标，无照片或插图；使用现有 Heroicons，不新增自绘图标或位图。
7. 内容：金额明确为估算，范围不明时不生成模型用量或预测。合成数据包含附加模型限额，因此标准窗口预测留空并解释原因；这是数据可靠性要求产生的差异。重置历史仅代表本系统发起的操作。
8. 最终并排比较未发现剩余 P0/P1/P2 问题。

## 交互、权限与回归

- 首次点击只打开确认框，显示目标账号、可用次数、将消耗 1 次和不可撤销说明。
- 桌面取消后仍为 1 次；手机再次确认后变为 0 次，按钮禁用并出现“已重置”记录。
- 后端 71 项测试通过：账号状态、CPA 采集集成、只读 API、车主/管理员权限、普通成员拒绝、跨账号拒绝、权限撤销、过期、二次确认、重复提交及未知结果复用标识。
- `makemigrations --check --dry-run` 无待生成迁移；前端 `pnpm check`、`pnpm build` 通过，演示构建也已验证。
- Sub2API 状态页保留原有周期与统计布局，截图 `sub2api-regression.png`。回归发现原历史图表缺少 ECharts Toolbox 注册，已补注册并隐藏工具栏，保留原拖选行为；修复后重新加载没有新增 console error。
- 重置真实上游交互以测试替身验证请求协议和幂等标识，线上额度未消耗。

## 后续优化

P3：上游提供可信模型归属范围后，可增加附加限额的窗口用量和预测。
