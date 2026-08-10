<script setup lang="ts">
import { computed, ref } from "vue";

const startCost = ref(120);
const endCost = ref(480);
const startPercent = ref(6);
const endPercent = ref(24);
const contractShare = ref(40);
const attributedShare = ref(18);
const showIntegerRange = ref(true);

const costDelta = computed(() => Math.max(0, endCost.value - startCost.value));
const percentDelta = computed(() =>
  Math.max(0.1, endPercent.value - startPercent.value),
);
const usdPerPercent = computed(() => costDelta.value / percentDelta.value);
const fullCapacity = computed(() => usdPerPercent.value * 100);
const remainingShare = computed(() =>
  Math.max(0, contractShare.value - attributedShare.value),
);
const recommendedBalance = computed(
  () => usdPerPercent.value * remainingShare.value,
);

const possibleStartLow = computed(() => startPercent.value);
const possibleStartHigh = computed(() => startPercent.value + 1);
const possibleEndLow = computed(() => endPercent.value);
const possibleEndHigh = computed(() => endPercent.value + 1);
const minimumTrueDelta = computed(() =>
  Math.max(0.1, possibleEndLow.value - possibleStartHigh.value),
);
const maximumTrueDelta = computed(() =>
  Math.max(
    minimumTrueDelta.value,
    possibleEndHigh.value - possibleStartLow.value,
  ),
);
const capacityRange = computed(() => ({
  low: (costDelta.value / maximumTrueDelta.value) * 100,
  high: (costDelta.value / minimumTrueDelta.value) * 100,
}));

const chartStart = computed(() => ({
  x: 72 + (startPercent.value / 50) * 550,
  y: 152 - (startCost.value / 800) * 112,
}));
const chartEnd = computed(() => ({
  x: 72 + (endPercent.value / 50) * 550,
  y: 152 - (endCost.value / 800) * 112,
}));
function inputValue(event: Event): number {
  return Number((event.currentTarget as HTMLInputElement).value);
}

function updateStartCost(event: Event) {
  startCost.value = Math.min(inputValue(event), endCost.value - 20);
}

function updateEndCost(event: Event) {
  endCost.value = Math.max(inputValue(event), startCost.value + 20);
}

function updateStartPercent(event: Event) {
  startPercent.value = Math.min(inputValue(event), endPercent.value - 2);
}

function updateEndPercent(event: Event) {
  endPercent.value = Math.max(inputValue(event), startPercent.value + 2);
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}
</script>

<template>
  <div class="algorithm-lesson divide-y divide-base-300">
    <section class="lesson-section">
      <div class="lesson-copy">
        <p>
          平均恒定模型从一个很直观的问题开始：在一段已经发生的区间里，每增加 1%
          周限，实际花掉了多少美元？它不尝试还原中间每一刻的容量变化，而是把整段区间压缩成一个平均值。
        </p>
        <p>
          这种做法透明、容易复核，适合快速总结一段历史。下面用两个端点完整走一遍计算。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">1</span>
        <div>
          <h3>选择同一周期里的两个端点</h3>
          <p>起点和终点都包含累计美元用量与页面显示的整数百分比。</p>
        </div>
      </div>

      <div class="endpoint-layout mt-6">
        <div class="demo-panel space-y-5">
          <div class="grid gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="flex justify-between text-sm"
                ><span>起点成本</span
                ><strong>{{ formatUsd(startCost) }}</strong></span
              >
              <input
                :value="startCost"
                type="range"
                min="0"
                max="800"
                step="10"
                class="range mt-2 w-full range-primary range-sm"
                @input="updateStartCost"
              />
            </label>
            <label class="block">
              <span class="flex justify-between text-sm"
                ><span>终点成本</span
                ><strong>{{ formatUsd(endCost) }}</strong></span
              >
              <input
                :value="endCost"
                type="range"
                min="0"
                max="800"
                step="10"
                class="range mt-2 w-full range-primary range-sm"
                @input="updateEndCost"
              />
            </label>
            <label class="block">
              <span class="flex justify-between text-sm"
                ><span>起点显示</span><strong>{{ startPercent }}%</strong></span
              >
              <input
                :value="startPercent"
                type="range"
                min="0"
                max="50"
                step="1"
                class="range mt-2 w-full range-secondary range-sm"
                @input="updateStartPercent"
              />
            </label>
            <label class="block">
              <span class="flex justify-between text-sm"
                ><span>终点显示</span><strong>{{ endPercent }}%</strong></span
              >
              <input
                :value="endPercent"
                type="range"
                min="0"
                max="50"
                step="1"
                class="range mt-2 w-full range-secondary range-sm"
                @input="updateEndPercent"
              />
            </label>
          </div>
        </div>

        <div class="demo-panel">
          <svg
            class="endpoint-chart"
            viewBox="0 0 700 190"
            role="img"
            aria-label="成本和周限百分比的两个端点"
          >
            <defs>
              <linearGradient id="average-flow" x1="0" x2="1">
                <stop offset="0" stop-color="var(--color-secondary)" />
                <stop offset="1" stop-color="var(--color-primary)" />
              </linearGradient>
              <filter
                id="average-glow"
                x="-80%"
                y="-80%"
                width="260%"
                height="260%"
              >
                <feGaussianBlur stdDeviation="3" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            <g class="chart-grid">
              <line x1="72" y1="152" x2="646" y2="152" />
              <line x1="72" y1="96" x2="646" y2="96" />
              <line x1="72" y1="40" x2="646" y2="40" />
            </g>
            <g class="fill-current text-[11px] opacity-45">
              <text x="64" y="156" text-anchor="end">$0</text>
              <text x="64" y="44" text-anchor="end">$800</text>
              <text x="72" y="180">0%</text>
              <text x="646" y="180" text-anchor="end">50%</text>
            </g>
            <line
              class="endpoint-line"
              :x1="chartStart.x"
              :y1="chartStart.y"
              :x2="chartEnd.x"
              :y2="chartEnd.y"
            />
            <g class="endpoint-point">
              <circle :cx="chartStart.x" :cy="chartStart.y" r="8" />
              <text
                :x="chartStart.x"
                :y="chartStart.y - 17"
                text-anchor="middle"
              >
                起点
              </text>
            </g>
            <g class="endpoint-point endpoint-point--end">
              <circle :cx="chartEnd.x" :cy="chartEnd.y" r="8" />
              <text :x="chartEnd.x" :y="chartEnd.y - 17" text-anchor="middle">
                终点
              </text>
            </g>
          </svg>
        </div>
      </div>

      <div class="watch-note">
        <AppIcon name="cursor-arrow-rays" class="size-5 shrink-0" />
        <p>
          拖动任意滑块。线段表示我们正在总结的完整区间，而不是在猜测线段中间每一刻发生了什么。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">2</span>
        <div>
          <h3>先做差，去掉起点以前的历史</h3>
          <p>累计数字本身很大，但我们只关心这两个端点之间新发生的部分。</p>
        </div>
      </div>

      <div class="calculation-strip mt-6">
        <div class="calculation-cell">
          <span>美元增加</span>
          <strong>{{ formatUsd(endCost) }} − {{ formatUsd(startCost) }}</strong>
          <em>= {{ formatUsd(costDelta) }}</em>
        </div>
        <AppIcon
          name="arrows-right-left"
          class="hidden size-5 opacity-35 sm:block"
        />
        <div class="calculation-cell">
          <span>百分比增加</span>
          <strong>{{ endPercent }}% − {{ startPercent }}%</strong>
          <em>= {{ percentDelta.toFixed(1) }}%</em>
        </div>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">3</span>
        <div>
          <h3>用美元增量除以百分比增量</h3>
          <p>先得到平均每 1% 值多少钱，再扩展到完整的 100%。</p>
        </div>
      </div>

      <div class="formula-card mt-6">
        <p class="formula">
          {{ formatUsd(costDelta) }} ÷ {{ percentDelta.toFixed(1) }}% =
          {{ formatUsd(usdPerPercent) }} / 1%
        </p>
        <div class="formula-divider"></div>
        <p class="formula formula--result">
          {{ formatUsd(usdPerPercent) }} × 100 = {{ formatUsd(fullCapacity) }} /
          周期
        </p>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          这个结果叫“完整周限等效额度”：如果这段区间里的平均兑换关系延续到
          100%，整份周限大约对应多少美元。它描述的是区间平均，不声称容量在每一刻都保持不变。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">4</span>
        <div>
          <h3>整数百分比会给答案留下一段范围</h3>
          <p>页面显示 6% 和 24%，内部真实值不一定恰好等于这两个整数。</p>
        </div>
      </div>

      <div class="demo-panel mt-6">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="font-semibold">把每个整数看成一个可能区间</p>
            <p class="mt-1 text-xs opacity-55">
              示意采用向下取整区间；实际模型会按已配置规则处理。
            </p>
          </div>
          <button
            class="btn btn-sm"
            :class="showIntegerRange ? 'btn-primary' : 'btn-ghost'"
            type="button"
            @click="showIntegerRange = !showIntegerRange"
          >
            <AppIcon name="adjustments-horizontal" class="size-4" />
            {{ showIntegerRange ? "隐藏区间" : "显示区间" }}
          </button>
        </div>

        <div class="integer-track mt-6">
          <div
            class="integer-segment"
            :style="{ left: `${startPercent * 2}%`, width: '2%' }"
          >
            <span>{{ startPercent }}%～{{ startPercent + 1 }}%</span>
          </div>
          <div
            class="integer-segment integer-segment--end"
            :style="{ left: `${endPercent * 2}%`, width: '2%' }"
          >
            <span>{{ endPercent }}%～{{ endPercent + 1 }}%</span>
          </div>
        </div>

        <div
          v-if="showIntegerRange"
          class="stats mt-6 w-full stats-vertical bg-base-200 sm:stats-horizontal"
        >
          <div class="stat py-4">
            <div class="stat-title text-xs">可能的真实跨度</div>
            <div class="stat-value text-lg">
              {{ minimumTrueDelta.toFixed(1) }}% ～
              {{ maximumTrueDelta.toFixed(1) }}%
            </div>
          </div>
          <div class="stat py-4">
            <div class="stat-title text-xs">对应容量范围</div>
            <div class="stat-value text-lg">
              {{ formatUsd(capacityRange.low) }} ～
              {{ formatUsd(capacityRange.high) }}
            </div>
          </div>
        </div>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          区间越短，最多 1%
          的端点误差就越显眼。因此系统不会把一个短区间算出的单点伪装成绝对精确值；统计页面会显示计算依据，跨度不足时还会明确标记“样本不足”。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">5</span>
        <div>
          <h3>再换算成参与者还能使用多少</h3>
          <p>平均每 1% 的美元价值，乘以该参与者尚未使用的权益。</p>
        </div>
      </div>

      <div class="balance-layout mt-6">
        <div class="demo-panel space-y-5">
          <label class="block">
            <span class="flex justify-between text-sm"
              ><span>合同权益</span><strong>{{ contractShare }}%</strong></span
            >
            <input
              v-model.number="contractShare"
              type="range"
              min="10"
              max="90"
              step="1"
              class="range mt-2 w-full range-primary range-sm"
            />
          </label>
          <label class="block">
            <span class="flex justify-between text-sm"
              ><span>已经归属</span
              ><strong>{{ attributedShare }}%</strong></span
            >
            <input
              v-model.number="attributedShare"
              type="range"
              min="0"
              max="100"
              step="1"
              class="range mt-2 w-full range-warning range-sm"
            />
          </label>
        </div>
        <div class="result-panel">
          <p class="text-xs font-medium tracking-wide opacity-55">
            按区间平均值计算的余额
          </p>
          <strong class="mt-2 block text-4xl text-primary tabular-nums">{{
            formatUsd(recommendedBalance)
          }}</strong>
          <p class="mt-5 text-sm font-semibold tabular-nums">
            ({{ contractShare }}% − {{ attributedShare }}%) ×
            {{ formatUsd(usdPerPercent) }}
          </p>
        </div>
      </div>
    </section>

    <section class="lesson-section">
      <div class="comparison-grid">
        <div class="comparison-card">
          <AppIcon name="check-circle" class="size-6 text-success" />
          <h3>它擅长什么</h3>
          <p>
            公式短、计算依据清楚，适合复核周期累计折算、日内折算和变化较平稳的历史区间。
          </p>
        </div>
        <div class="comparison-card">
          <AppIcon name="information-circle" class="size-6 text-info" />
          <h3>它没有回答什么</h3>
          <p>
            它不会重建区间中间的容量路径，也不会同时判断多种整数显示规则和变化速度。
          </p>
        </div>
      </div>

      <div
        class="mt-5 rounded-box border border-primary/25 bg-primary/5 p-5 sm:p-6"
      >
        <h3 class="font-semibold">怎样选择模型</h3>
        <p class="mt-3 max-w-4xl text-sm leading-7 opacity-75">
          如果你要的是一段历史“平均折算成多少”，平均恒定模型最容易解释；如果你要在容量持续变化时尽量贴近每一个当前时刻，粒子滤波会利用更多信息。两者不是互相替代：额度统计保留简单端点公式，动态额度建议可以使用粒子滤波。
        </p>
        <div class="mt-5 flex flex-wrap gap-3">
          <RouterLink to="/statistics" class="btn btn-primary btn-sm"
            >查看额度统计</RouterLink
          >
          <RouterLink
            to="/tutorial?page=particle-filter-algorithm"
            class="btn btn-ghost btn-sm"
            >继续阅读粒子滤波</RouterLink
          >
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.lesson-section {
  padding-block: 1.75rem;
}

.lesson-copy {
  max-width: 56rem;
  display: grid;
  gap: 0.75rem;
  font-size: 0.925rem;
  line-height: 1.85;
  color: color-mix(in oklab, currentColor 76%, transparent);
}

.lesson-heading {
  display: flex;
  align-items: flex-start;
  gap: 0.9rem;
}

.lesson-heading h3 {
  font-size: 1.15rem;
  font-weight: 700;
}

.lesson-heading p {
  margin-top: 0.3rem;
  font-size: 0.875rem;
  line-height: 1.5rem;
  opacity: 0.62;
}

.step-number {
  display: grid;
  width: 2rem;
  height: 2rem;
  flex: none;
  place-items: center;
  border-radius: 9999px;
  background: color-mix(in oklab, var(--color-primary) 16%, transparent);
  color: var(--color-primary);
  font-size: 0.82rem;
  font-weight: 800;
}

.demo-panel,
.result-panel {
  border: 1px solid var(--color-base-300);
  border-radius: var(--radius-box);
  background: var(--color-base-100);
  padding: 1.1rem;
}

.endpoint-layout,
.balance-layout,
.comparison-grid {
  display: grid;
  gap: 1rem;
}

.endpoint-chart {
  display: block;
  width: 100%;
}

.chart-grid line {
  stroke: currentColor;
  stroke-width: 1;
  stroke-dasharray: 3 7;
  opacity: 0.13;
}

.endpoint-line {
  stroke: url(#average-flow);
  stroke-width: 5;
  stroke-linecap: round;
  stroke-dasharray: 10 8;
  animation: average-flow 1.1s linear infinite;
}

.endpoint-point circle {
  fill: var(--color-secondary);
  filter: url(#average-glow);
  transition:
    cx 260ms ease,
    cy 260ms ease;
}

.endpoint-point--end circle {
  fill: var(--color-primary);
}

.endpoint-point text {
  fill: currentColor;
  font-size: 12px;
  font-weight: 700;
}

.watch-note {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  margin-top: 1rem;
  border-radius: var(--radius-box);
  background: color-mix(in oklab, var(--color-info) 11%, transparent);
  padding: 0.9rem 1rem;
  font-size: 0.85rem;
  line-height: 1.55rem;
}

.calculation-strip {
  display: grid;
  align-items: center;
  gap: 0.75rem;
}

.calculation-cell {
  display: grid;
  gap: 0.35rem;
  border: 1px solid var(--color-base-300);
  border-radius: var(--radius-box);
  background: var(--color-base-100);
  padding: 1rem;
  text-align: center;
}

.calculation-cell span {
  font-size: 0.75rem;
  opacity: 0.52;
}

.calculation-cell strong {
  font-size: 1rem;
  font-variant-numeric: tabular-nums;
}

.calculation-cell em {
  color: var(--color-primary);
  font-size: 1.15rem;
  font-style: normal;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.formula-card {
  border: 1px solid
    color-mix(in oklab, var(--color-primary) 30%, var(--color-base-300));
  border-radius: var(--radius-box);
  background: color-mix(
    in oklab,
    var(--color-primary) 6%,
    var(--color-base-100)
  );
  padding: 1.4rem;
  text-align: center;
}

.formula {
  font-size: clamp(1rem, 2.3vw, 1.35rem);
  font-weight: 750;
  font-variant-numeric: tabular-nums;
}

.formula--result {
  color: var(--color-primary);
  font-size: clamp(1.15rem, 2.8vw, 1.65rem);
}

.formula-divider {
  width: min(18rem, 70%);
  height: 1px;
  margin: 1rem auto;
  background: var(--color-base-300);
}

.integer-track {
  position: relative;
  height: 5.5rem;
  border-radius: var(--radius-box);
  background:
    repeating-linear-gradient(
      90deg,
      transparent 0 calc(10% - 1px),
      color-mix(in oklab, currentColor 12%, transparent) calc(10% - 1px) 10%
    ),
    var(--color-base-200);
}

.integer-segment {
  position: absolute;
  top: 1rem;
  min-width: 0.9rem;
  height: 3.5rem;
  border-radius: 0.35rem;
  background: var(--color-secondary);
  box-shadow: 0 0 18px
    color-mix(in oklab, var(--color-secondary) 35%, transparent);
}

.integer-segment--end {
  background: var(--color-primary);
  box-shadow: 0 0 18px
    color-mix(in oklab, var(--color-primary) 35%, transparent);
}

.integer-segment span {
  position: absolute;
  top: 50%;
  left: 50%;
  width: max-content;
  transform: translate(-50%, -50%);
  border-radius: 9999px;
  background: var(--color-base-100);
  padding: 0.25rem 0.45rem;
  font-size: 0.68rem;
  font-weight: 700;
  white-space: nowrap;
}

.result-panel {
  display: flex;
  min-height: 12rem;
  flex-direction: column;
  justify-content: center;
  text-align: center;
}

.comparison-card {
  border: 1px solid var(--color-base-300);
  border-radius: var(--radius-box);
  background: var(--color-base-100);
  padding: 1.2rem;
}

.comparison-card h3 {
  margin-top: 0.85rem;
  font-weight: 700;
}

.comparison-card p {
  margin-top: 0.55rem;
  font-size: 0.875rem;
  line-height: 1.65rem;
  opacity: 0.68;
}

@keyframes average-flow {
  to {
    stroke-dashoffset: -18;
  }
}

@media (min-width: 40rem) {
  .calculation-strip {
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  }
}

@media (min-width: 48rem) {
  .demo-panel,
  .result-panel {
    padding: 1.35rem;
  }

  .endpoint-layout,
  .balance-layout,
  .comparison-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (prefers-reduced-motion: reduce) {
  .endpoint-line {
    animation: none;
  }

  .endpoint-point circle {
    transition-duration: 0.01ms;
  }
}
</style>
