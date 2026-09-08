# CPA 请求明细设计验收

final result: passed

## 设计目标与证据

将已有 CPA 请求列表改为独立菜单，以用户提供的 CPA Manager Plus 截图为信息层级参考，融入 Sub2Pool 现有 daisyUI 主题、导航和授权。这是现有产品内的改造，不是第三方界面的逐像素复刻。

- Source visual truth: 用户附件 `codex-clipboard-fcc2e85f-6aa8-4c02-a959-b1f3dd3399c2.png`，2542 × 1364 px；为裁剪截图，原 CSS viewport / DPR 未知。
- Implementation: `http://127.0.0.1:5180/#/cpa-requests`，合成数据管理员，最近 7 天、全部模型 / Key / 状态，补价后。
- Desktop screenshot: `/tmp/sub2pool-request-design/desktop.png`，1440 × 1050 px，CSS viewport 1440 × 1050，DPR 1。
- Mobile screenshots: `/tmp/sub2pool-request-design/mobile-cards.png`、`/tmp/sub2pool-request-design/mobile-detail.png`，390 × 844 px，CSS viewport 390 × 844，DPR 1。
- Full-view comparison: `/tmp/sub2pool-request-design/full-comparison.png`；两图按相同展示宽度等比缩小。来源截图已裁去标题和部分指标，不以顶部高度差认定偏差。
- Focused comparison: `/tmp/sub2pool-request-design/comparison.png`；裁出指标和请求表格区域并并排比较，检查层级、行高、字段分组与数字对齐。
- 截图保存在本地验收目录，未上传第三方。

## 检查结果

- 字体：沿用系统字体；指标大字、标签小字；数值使用等宽数字，模型名截断并提供完整详情。表格和手机卡片均可读。
- 布局：两行四列指标；筛选与结果范围分开；桌面表格分组展示模型、状态、Token、缓存、响应、费用和时间。390 px 下指标和筛选两列，请求改为卡片，无页面横向溢出。
- 颜色：沿用现有明暗主题。数字使用正文色，状态同时提供文字与底色；无需依赖颜色辨认成功 / 失败。
- 图像与图标：使用项目现有 Heroicons。参考截图中的装饰曲线和状态历史条未引入；当前没有对应时序数据，不绘制装饰性假趋势。无需新增图片资产。
- 内容：摘要计算整个筛选范围而非当前页；费用明确为估算；未知价格保留缺价数量；缺少有效耗时展示破折号。单次详情包含完整请求 ID、端点和服务等级，不展示正文。
- 权限：新菜单复用 statistics 页面授权，后端汇总在既有账号、参与者、Key、时间和状态过滤之后计算；只读 API 同样受限。

## 比较与修正记录

1. 首轮发现 [P2] 深色主题下彩色指标数字对比度偏低；改为正文色，保留图标点缀。状态文字也使用正文色。最终桌面、移动截图确认可读。
2. 首轮发现 [P2] 手机筛选单列占据过多首屏空间；调整为两列，操作按钮独占一行。390 px 检查确认控件不裁切，页面 scrollWidth 等于 innerWidth。
3. 最终全景与局部并排比较：无剩余 P0/P1/P2 问题。参考的蓝白背景、字体和实时分组保留为来源产品特点；实现使用现有 Sub2Pool 主题和真实可用字段。

## 功能验证

- 独立菜单进入、统计页携带账号跳转。
- 演示 84 条请求；筛选失败后 5 条；分页进入第 2 / 4 页。
- 一键补价从 21 次缺价变为 0，费用由 $2.99 更新为 $6.68。
- 桌面与手机打开 / 关闭单次详情；手机页面宽度 390 px，无横向溢出。
- 浏览器 console error 日志为空。
- 38 项后端请求、定价、参与者与只读权限测试通过；前端 check、build 和 build:demo 通过。

## 后续优化

P3：若后续提供完整请求趋势与推理等级事实，可增加真实趋势和推理等级筛选。当前不影响请求查询任务。
