# 粒子滤波研究总览

本目录归档共享周期资源动态限额问题的数学研究、算法比较和工程化实验。目标不是重复后端实现，而是保留“为什么采用当前方案、哪些替代方案被否决、结论由哪些实验支持”的可复现实证链。

## 1. 研究问题

设一个周期长度为 $T$，隐藏的总资源等效金额为连续函数 $V(t)$。参与者 $i$ 的累计美元消耗为 $C_i(t)$，其真实资源进度为

$$
Q_i(t)=\int_0^t \frac{100}{V(s)}\,\mathrm dC_i(s),
\qquad
P(t)=\sum_i Q_i(t).
$$

系统只能间歇观测：

- 每名参与者累计消耗 $C_i(t_k)$；
- 被量化为整数的总进度 $Z_k=\mathcal Q(P(t_k))$；
- 合同权益比例 $h_i$；
- 容量的先验范围和连续性假设。

需要在线估计：

$$
B_i(t)=\left[100h_i-Q_i(t)\right]_+\frac{V(t)}{100},
$$

即每名参与者当前应有的美元余额。评价对象是整个连续周期上的余额建议，不只是采样瞬间的容量点估计。

初始论文主要研究 $V(t)\in[1400,2100]$。工程阶段根据真实使用范围将标准搜索区间改为 $[1400,4000]$，并研究了向上和向下的分级扩张。所有合成实验中的容量路径连续；跳变不属于当前模型。

## 2. 最终设计

当前结论是三层结构：

1. **粒子滤波点估计**：并行保留多条可能的容量路径、量化规则、区间内消费时序和速度尺度，根据新观测逐次更新权重；输出容量和参与者归属的后验代表值。
2. **概率区间**：由粒子分布给出较窄的常用不确定性区间；区间覆盖需要独立校准。
3. **确定性边界与投影**：由金额精度、整数进度单元和容量范围推导必然可行范围；点估计不得落入已知不可能区域。它是安全约束，不是另一个主点估计算法。

标准搜索区间为 $1400\sim4000$。若最近 5% 边界带中的粒子质量至少为 10%，且同方向整数显示残差超过 0.05 个百分点，则立即扩张：

- 向上：$6000\rightarrow10000\rightarrow20000$；
- 向下：$700\rightarrow250\rightarrow50$。

同一周期内不自动收缩；新周期重新从标准范围开始。当前生产默认仍为 480 个粒子。实验 10 的 960 粒子与条件区间校准是下一版候选，不代表生产配置已经修改。

## 3. 目录与复现约定

```text
docs/study/
├── README.md                         # 本文
├── scripts/                          # 01～10：按研究顺序编号的入口
│   └── _support/                     # 入口脚本依赖的原始研究模块
├── src/dynamic_limit/                # 核心合成模型和粒子算法快照
├── config/                           # 固定种子、矩阵与参数网格
├── evidence/                         # 已完成实验的精简结果证据
│   ├── attribution/                  # 可识别性与精确可行集研究
│   └── core/                         # 粒子滤波和工程实验
└── results/                          # 重跑输出；仅预置冻结参数
```

`evidence/` 只保存足以核对结论的汇总表和元数据；数 MB 的逐轨迹原始表不重复提交。入口脚本会重新生成原始表。`_support/` 保留依赖模块的原始名称，因为这些名称参与 Python 导入；对外入口统一按 `01`～`10` 排序。
`_support/attribution/run_study.py` 是为延迟、历史、交互和并发事件等辅助入口保留的可导入副本；编号 `01` 仍是对外主入口。分析脚本统一把新结果写入 `results/`，不会修改 `evidence/` 中的冻结证据。


从仓库根目录运行时，可使用：

```bash
uv run --directory backend \
  --with pandas --with scipy --with pyyaml --with joblib \
  python ../docs/study/scripts/<脚本名> --help
```

完整矩阵可能持续较久并生成大量 CSV。工程实验优先使用其 `--smoke` 或较小案例参数验证流水线，再运行冻结矩阵。结果目录中的“最大误差”均为固定有限样本里的观测最大值，不是数学最坏界。

## 4. 实验 01：可识别性、精确可行集与传统归属法

入口：[scripts/01_identifiability_and_exact_set.py](scripts/01_identifiability_and_exact_set.py)

配置：[config/attribution_study.yaml](config/attribution_study.yaml)

证据：

- [evidence/attribution/final_key_values.json](evidence/attribution/final_key_values.json)
- [evidence/attribution/main_fast_final_summary.csv](evidence/attribution/main_fast_final_summary.csv)
- [evidence/attribution/main_lp_final_summary.csv](evidence/attribution/main_lp_final_summary.csv)
- [evidence/attribution/validation_report.json](evidence/attribution/validation_report.json)

### 方法

先回答“信息是否足够”而不是直接挑算法。实验构造完整事件级数据，在相同时间发生的消费共享同一个逆容量变量，并用线性规划计算所有与观测相容的归属。随后比较：全周期比例、固定窗口、移动窗口、多相位、最小总变差、可行集中心和极小极大中心。

除主分布外，还检查整数显示规则错配、观测延迟、同一时刻并发事件、跨周期历史、边界饱和和主动搜索到的困难输入。开发、验证和最终矩阵使用固定但互不重叠的种子。

### 结果

- 仅凭连续性、容量上下界和整数进度，个体归属一般**不可唯一识别**；精确可行集在 10,000 个周期中覆盖率为 100%，平均宽度 1.940 个百分点。
- 精确事件级理想条件下，最小总变差点估计 MAE 为 0.190pp，可行集盒中点为 0.223pp；冻结多相位为 0.987pp，固定 5pp 窗口为 1.136pp，全周期比例为 1.417pp。
- 极小极大解所在的最优面经常不唯一；必须明确点选择规则，不能把求解器任意返回值当成唯一答案。
- 跨周期历史未显示稳定收益；显示规则错配可能使可行集不可行，必须显式报告，而不是悄悄给结果。

这一章确定了后续设计原则：点建议必须伴随不确定性边界；多相位只能作为候选/诊断，不能凭直觉充当最终主算法。

## 5. 实验 02：候选算法和粒子参数调优

入口：[scripts/02_parameter_tuning.py](scripts/02_parameter_tuning.py)

主要配置：[config/tuning.yaml](config/tuning.yaml)、[config/tuning_standard_1400_4000.yaml](config/tuning_standard_1400_4000.yaml)

冻结参数：[results/selected_parameters.json](results/selected_parameters.json)、[results/standard_1400_4000_selected_parameters.json](results/standard_1400_4000_selected_parameters.json)

### 方法

在独立调优集上分别选择：单窗口宽度、多尺度窗口组合、多相位宽度、自动延长阈值、粒子隐状态波动、观测软误差、区间内消费时序先验、概率区间膨胀系数和保护性惯性。主选择指标是全周期参与者余额建议的平均美元 MAE；案例级 P95 单独保留，不混成主观总分。

区间系数取达到目标样本覆盖率的最小值。所有参数冻结后，才进入第 3 章最终测试，避免在测试集上继续调参。

### 结论

- 窗口法需要按百分点跨度而不是固定时间跨度调节，但不同容量速度和消费形态不存在统一最优窗口。
- 粒子模型必须同时表示容量路径、量化不确定性和采样间隔内消费时序；只调容量噪声不足以解释数据。
- 调优结果是后续对比的冻结输入，不单独证明算法优越。
`standard_1400_4000_selected_parameters.json` 中的 `particle_count=320` 是调优矩阵的计算预算，不是生产粒子数；生产默认 480，实验 10 才单独比较粒子数。


## 6. 实验 03：主算法最终比较

入口：[scripts/03_final_algorithm_benchmark.py](scripts/03_final_algorithm_benchmark.py)

配置：[config/final_test.yaml](config/final_test.yaml)

证据：

- [evidence/core/final_aggregate.csv](evidence/core/final_aggregate.csv)
- [evidence/core/pairwise_mae_comparisons.csv](evidence/core/pairwise_mae_comparisons.csv)
- [evidence/core/report_key_results.json](evidence/core/report_key_results.json)

### 方法

最终矩阵包含 1,134 个主案例和 216 个压力案例，覆盖 2/4/6 名参与者、三种权益结构、11 种消费形态、三种采样间隔、三种整数显示规则和多种容量速度。该论文阶段矩阵使用初始 `1400～2100` 容量范围；所有算法处理相同观测，真值只用于计算误差，因此结果不能与后续 `1400～4000` 工程矩阵直接横比。

主指标为整个周期、所有参与者余额建议的美元 MAE。另报告案例 P95、最大观测误差、最差参与者、正负偏差、调整波动、概率区间覆盖和确定性区间宽度。

### 结果

| 方法 | 平均 MAE |
|---|---:|
| 粒子滤波 | 28.52 美元 |
| 多尺度窗口 | 31.67 美元 |
| 多相位窗口 | 31.78 美元 |
| 单窗口 | 31.91 美元 |
| 自动延长窗口 | 32.78 美元 |
| 全周期金额比例 | 33.20 美元 |
| 固定容量 | 40.20 美元 |

粒子滤波的 95% Bootstrap 区间为 27.52～29.52 美元，案例级 P95 为 63.63 美元。其概率区间样本覆盖率为 91.09%，平均宽度 100.18 美元；确定性外包络覆盖率为 100%，但平均宽度 192.95 美元。结论是粒子滤波最适合日常点建议，确定性区间适合审计和安全保护。

## 7. 实验 04：采样频率、量化误差与信息价值

入口：[scripts/04_sampling_and_sensitivity.py](scripts/04_sampling_and_sensitivity.py)

证据：

- [evidence/core/sampling_tradeoff.csv](evidence/core/sampling_tradeoff.csv)
- [evidence/core/sensitivity_summary.csv](evidence/core/sensitivity_summary.csv)

### 方法

消融实验分别替换为：精确金额、已知显示规则、精确总进度、真实当前容量、真实参与者归属和完整真实采样状态。这样可将总误差分解为状态估计误差、金额精度、整数进度和采样保持误差，而不是把所有改善错误归因于一个因素。

同时比较 1/3/6 小时采样，并分开记录采样瞬时误差、两次采样间保持误差和建议调整次数。

### 结果

- 粒子滤波 1/3/6 小时的 MAE 分别为 25.91、28.46、31.18 美元；1 小时最准，3 小时是准确度与操作频率的折中。
- 真实采样状态仍需承担 12.75 美元的保持误差，说明离散采样本身构成明显下限。
- 保留粒子归属但给真实当前容量时 MAE 为 13.09 美元；保留真实归属但估计容量时为 27.86 美元。当前容量估计是主要误差来源。
- 已知整数显示规则只把粒子 MAE 从 28.20 降到 27.74 美元；金额分精度影响很小。
- 窗口法获得精确进度后从 31.13 降到 26.91 美元，说明整数显示对窗口法影响更大。

## 8. 实验 05：极端连续场景与兜底策略

入口：[scripts/05_extreme_stress_and_fallback.py](scripts/05_extreme_stress_and_fallback.py)

配置：[config/extreme_study.yaml](config/extreme_study.yaml)、[config/extreme_study_standard_1400_4000.yaml](config/extreme_study_standard_1400_4000.yaml)

证据：

- [evidence/core/extreme_test_summary.csv](evidence/core/extreme_test_summary.csv)
- [evidence/core/extreme_selected_fallbacks.json](evidence/core/extreme_selected_fallbacks.json)
- [evidence/core/standard_1400_4000_extreme_test_summary.csv](evidence/core/standard_1400_4000_extreme_test_summary.csv)
- [evidence/core/standard_1400_4000_extreme_complementarity.json](evidence/core/standard_1400_4000_extreme_complementarity.json)
- [evidence/core/extreme_statistical_comparisons.csv](evidence/core/extreme_statistical_comparisons.csv)
- [evidence/core/extreme_factor_findings.csv](evidence/core/extreme_factor_findings.csv)
- [evidence/core/extreme_design_balance.json](evidence/core/extreme_design_balance.json)


### 方法

增加快速满幅扫描、啁啾、末段反转、窄脉冲、边界切换、随机傅里叶和双反转容量路径，并加入静默后爆发、首日鲸吞、交替尖峰、采样边缘尖峰、容量相关消费和极端权益偏斜。开发集搜索 77 类可观测兜底规则，冻结测试集只评估入选规则；`scripts/_support/engineering/summarize_extreme_study.py` 对原始矩阵执行配对 Bootstrap、Holm 校正、设计平衡和因素分层分析。

### 结果

- 在初始范围实验的 768 个极端案例中，粒子滤波仍保持最低平均误差，但并非每条路径都获胜；末段反转等局部场景窗口法可能更好。
- 可观测兜底策略未能稳定改善整体结果；入选兜底反而小幅增加 MAE，因此不自动切换主算法。
- 在标准范围扩大到 `1400～4000` 的新极端矩阵中，粒子滤波平均 MAE 为 185.83 美元，仍优于其他候选；数值不能与旧范围实验直接横比。
- 极端分布中的概率区间会失准；确定性外包络继续承担安全边界。

## 9. 实验 06：真实数据库只读回放

入口：[scripts/06_real_database_replay.py](scripts/06_real_database_replay.py)

证据：

- [evidence/core/expanded_real_replay.json](evidence/core/expanded_real_replay.json)
- [evidence/core/standard_1400_4000_production_replay.json](evidence/core/standard_1400_4000_production_replay.json)

### 方法

编号入口只读取 SQLite 副本，不访问网络、不修改真实 Sub2API；它重建模型输入并比较迁移阶段的旧 `1400～2100` 与候选 `1000～3500` 范围、不同粒子数和随机种子。另一个生产回放验证器 `scripts/_support/engineering/verify_copied_database_rebuild.py` 会在数据库副本上调用生产重建链，生成 `1400～4000` 验收摘要。两者用途不同，不能把后一份证据当作编号入口的直接输出。

示例：

```bash
uv run --directory backend --with pandas --with scipy --with pyyaml \
  python ../docs/study/scripts/06_real_database_replay.py \
  --backend . \
  --data-dir ../data-copy \
  --selected ../docs/study/results/standard_1400_4000_selected_parameters.json \
  --output ../docs/study/results/real-replay.json
```

生产重建链验证：

```bash
uv run --directory backend \
  python ../docs/study/scripts/_support/engineering/verify_copied_database_rebuild.py \
  --backend . \
  --data-dir ../data-copy \
  --output ../docs/study/results/production-replay.json
```

### 结论边界

比较回放证据包含 78 条观测；生产重建证据包含 82 条观测，其中 25 条被当时的 `particle_filter_v3` 诊断为不相容、42 条触发过投影。后者是历史迁移验收快照，不代表当前 v4 在同一数据库上的现状。两份回放证明输入链、重建和诊断能够运行，也促成搜索范围扩大；但真实隐藏容量不可见，因此不能证明估计值“接近真值”。

## 10. 实验 07：边界扩张触发条件

入口：[scripts/07_boundary_expansion_trigger.py](scripts/07_boundary_expansion_trigger.py)

证据：

- [evidence/core/adaptive_boundary_study.json](evidence/core/adaptive_boundary_study.json)
- [evidence/core/expansion_strategy_study.json](evidence/core/expansion_strategy_study.json)

### 方法

标准范围固定为 `1400～4000`，先只研究“何时扩张”，避免把触发条件和扩张幅度混在一起。800 个案例分成 400 个开发案例和 400 个冻结案例，共比较 427 组规则：点估计距离、边界粒子质量、同方向显示残差、连续命中次数以及单边影子滤波确认。

### 结果

冻结集平均 MAE：固定范围 149.39 美元，永久宽范围 152.42 美元，双证据直接扩张 136.40 美元，影子确认 136.62 美元，知道真实方向的 Oracle 133.26 美元。直接扩张比影子确认平均好约 0.22 美元，影子还增加计算，因此不采用影子。

冻结规则为：最近 5% 边界带粒子质量至少 10%，同方向显示残差大于 0.05pp，一次命中立即扩张。它在“已经出现可观测边界证据”的案例中检出率为 98.32%，冻结集错误方向率为 0.71%。

## 11. 实验 08：扩张幅度与分级范围

入口：[scripts/08_boundary_expansion_range.py](scripts/08_boundary_expansion_range.py)

证据：

- [evidence/core/expansion_range_analysis.json](evidence/core/expansion_range_analysis.json)
- [evidence/core/expansion_range_confirmation_analysis.json](evidence/core/expansion_range_confirmation_analysis.json)

### 方法

固定实验 07 的触发规则，只改变扩张目标。编号入口生成主矩阵原始结果；`analyze_expansion_ranges.py` 完成首次筛选。随后用新的 `replication_salt` 重跑独立矩阵，再由 `analyze_expansion_range_confirmation.py` 比较入围策略，最后由 `verify_expansion_range_study.py` 检查两轮各 1,120 个案例、115 个候选及最终决策不变量。开发、冻结、尾部和独立复现实验使用不同种子。

`staged_ratio_like` 是首次分析的候选；最终采用 `staged_very_coarse` 的依据是第二轮独立确认。仅运行编号入口不会直接生成最终确认文件。

### 结果

- 普通分布中，原单级 `1400～6000` / `700～4000` 与粗分级差异近乎为零。
- 尾部 320 个案例中，原单级 MAE 为 257.68 美元；粗分级到 `6000/10000/20000` 和 `700/250/50` 后为 233.31 美元，改善约 24.38 美元。
- 从一开始永久使用 `50～20000` 在普通分布中的 MAE 为 693.54 美元，明显破坏粒子密度和估计精度。

因此只在双证据触发后按需分级扩大，而不是预先使用极宽地图。

## 12. 实验 09：周期内自动收缩

入口：[scripts/09_range_contraction.py](scripts/09_range_contraction.py)

证据：[evidence/core/range_contraction_study.json](evidence/core/range_contraction_study.json)

### 方法

比较 378 个因果收缩候选，覆盖临时越界、回归、反弹、持续越界和范围内贴边共 10 类连续路径。候选使用概率区间回归、连续命中、冷却期、最小保留级别和影子确认；开发/冻结共 800 个案例，再用 600 个新案例确认。

### 结果

最好的自动收缩候选相对“不收缩”仍增加 0.740 美元 MAE，95% Bootstrap 区间为 `[0.203, 1.269]` 美元；其余入选者更差。原因是“此刻回到标准范围”不能证明之后不会再次越界，收缩会稀释既有后验并造成再扩张损失。

决策：同一周期只扩不缩；新周期自然重置为标准范围。

## 13. 实验 10：确定性投影、区间校准与粒子数

入口：[scripts/10_projection_interval_particles.py](scripts/10_projection_interval_particles.py)

证据：

- [evidence/core/production_robustness_study.json](evidence/core/production_robustness_study.json)
- [evidence/core/production_robustness_point_summary.csv](evidence/core/production_robustness_point_summary.csv)
- [evidence/core/production_robustness_interval_summary.csv](evidence/core/production_robustness_interval_summary.csv)
- [evidence/core/production_robustness_particle_adjacent_comparison.csv](evidence/core/production_robustness_particle_adjacent_comparison.csv)
- [evidence/core/production_robustness_particle_reference_comparison.csv](evidence/core/production_robustness_particle_reference_comparison.csv)

### 方法

直接调用生产版自适应范围滤波和确定性投影，使用 300 个开发案例、600 个独立确认案例及重复随机种子。消融投影；按扩张方向和级别校准 90% 区间；比较 120/240/320/480/960/1920 粒子及运行时间。

### 结果

- 480 粒子下，投影令 MAE 平均变化 +0.036 美元，95% 区间 `[-0.015, 0.090]` 美元，统计上近乎中性；但它阻止已知不可能输出，所以保留。
- 扩张场景不能共用一个固定区间膨胀系数。候选组合为：第一级上扩使用确定性外包络，第一级下扩 1.3，第二级以上双向均 1.0。独立确认覆盖率 95.04%，95% 区间 94.20%～95.81%，平均宽度 441.53 美元。
- 960 相比 480 的确认集 MAE 低 1.95 美元，95% 区间 `[0.75, 3.34]` 美元；相比 1920 只高 0.67 美元，区间跨 0。960 运行时间约为 480 的 1.63 倍、1920 的 57%。研究建议下一版采用 960，但生产默认是否迁移必须另行做运行成本和回放验收。

## 14. 结论与禁止误读

1. 粒子滤波是在当前观测约束下，平均最贴近连续真值路径的已验证点估计；它不是数学上无条件最优，也不是每个案例都胜出。
2. 边界扩张解决模型范围错配，不能弥补没有消费和百分比变化所导致的无信息区间。
3. 确定性区间给出审计边界，概率区间给出较窄的常用不确定性；二者不能互相替代。
4. 真实数据库回放没有隐藏真值，只能验证工程链路和结果一致性；准确度结论来自有真值的连续合成实验。
5. 本目录是研究快照。生产职责、数据库重放和界面语义仍以 `docs/particle-filter.md` 及 `backend/monitor/accounting/` 为准；研究脚本不得被线上请求直接调用。
