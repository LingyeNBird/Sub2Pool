<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

interface Observation {
  label: string;
  cost: number;
  percent: number;
}

interface CandidateDot {
  capacity: number;
  x: number;
  y: number;
}
type DisplayRule = "floor" | "round" | "ceil";

const observations: Observation[] = [
  { label: "周期起点", cost: 0, percent: 0 },
  { label: "第 1 次观测", cost: 252, percent: 14 },
  { label: "第 2 次观测", cost: 560, percent: 27 },
  { label: "第 3 次观测", cost: 735, percent: 37 },
  { label: "第 4 次观测", cost: 1035, percent: 49 },
  { label: "第 5 次观测", cost: 1230, percent: 59 },
];

const candidates: CandidateDot[] = Array.from({ length: 60 }, (_, index) => {
  const ratio = index / 59;
  return {
    capacity: 1400 + ratio * 2600,
    x: 42 + ratio * 616,
    y: 34 + ((Math.sin((index + 1) * 2.399) + 1) / 2) * 102,
  };
});

const playhead = ref(0);
const playing = ref(false);
const hiddenPercent = ref(25.6);
const displayRule = ref<DisplayRule>("round");
const selectedSpeed = ref<6 | 24 | 72>(24);
const resampleStage = ref<0 | 1 | 2>(0);
const contractShare = ref(50);
const attributedShare = ref(22);
const estimatedCapacity = ref(1900);
const reducedMotion = ref(false);
let animationFrame = 0;
let previousFrameTime = 0;
let motionQuery: MediaQueryList | null = null;
let resampleTimers: number[] = [];

const completedStep = computed(() => Math.floor(playhead.value));
const stepMix = computed(() => playhead.value - completedStep.value);
const currentObservation = computed(() => {
  const left = observations[completedStep.value] ?? observations[0];
  const right =
    observations[Math.min(completedStep.value + 1, observations.length - 1)];
  const mix = stepMix.value;
  return {
    label: mix > 0.02 ? `${left.label} → ${right.label}` : left.label,
    cost: left.cost + (right.cost - left.cost) * mix,
    percent: Math.round(left.percent + (right.percent - left.percent) * mix),
  };
});

function quantizerDistance(expected: number, observed: number): number {
  return Math.min(
    Math.abs(Math.floor(expected) - observed),
    Math.abs(Math.round(expected) - observed),
    Math.abs(Math.ceil(expected) - observed),
  );
}

function intervalCandidateScore(capacity: number, index: number): number {
  if (index <= 0) return 1;
  const current = observations[index];
  const previous = observations[index - 1];
  const costDelta = current.cost - previous.cost;
  const percentDelta = current.percent - previous.percent;
  const expectedDelta = (costDelta / capacity) * 100;
  const distance = quantizerDistance(expectedDelta, percentDelta);
  return Math.max(0.035, Math.exp(-distance * distance * 0.55));
}

function candidateScore(capacity: number): number {
  if (playhead.value <= 0) return 1;
  const leftIndex = Math.min(completedStep.value, observations.length - 1);
  const rightIndex = Math.min(leftIndex + 1, observations.length - 1);
  const leftScore = intervalCandidateScore(capacity, leftIndex);
  const rightScore = intervalCandidateScore(capacity, rightIndex);
  return leftScore + (rightScore - leftScore) * stepMix.value;
}

const scoredCandidates = computed(() =>
  candidates.map((candidate) => {
    const score = candidateScore(candidate.capacity);
    return {
      ...candidate,
      score,
      radius: 2.5 + score * 4.8,
      opacity: 0.12 + score * 0.88,
    };
  }),
);

const candidateConclusion = computed(() => {
  const weighted = scoredCandidates.value.reduce(
    (result, candidate) => {
      result.sum += candidate.capacity * candidate.score;
      result.weight += candidate.score;
      return result;
    },
    { sum: 0, weight: 0 },
  );
  return weighted.weight > 0 ? weighted.sum / weighted.weight : 0;
});

const displayRuleOptions: { id: DisplayRule; label: string }[] = [
  { id: "floor", label: "向下取整" },
  { id: "round", label: "四舍五入" },
  { id: "ceil", label: "向上取整" },
];

function quantize(rule: DisplayRule, value: number): number {
  if (rule === "floor") return Math.floor(value);
  if (rule === "ceil") return Math.ceil(value);
  return Math.round(value);
}

const displayRuleResults = computed(() =>
  displayRuleOptions.map((rule) => ({
    ...rule,
    value: quantize(rule.id, hiddenPercent.value),
  })),
);
const displayedPercent = computed(() =>
  quantize(displayRule.value, hiddenPercent.value),
);

const speedPaths = computed(() =>
  ([6, 24, 72] as const).map((hours) => {
    const points = Array.from({ length: 37 }, (_, index) => {
      const elapsed = index * 2;
      const capacity = 1800 + 500 * (1 - Math.exp(-elapsed / hours));
      const x = 36 + (elapsed / 72) * 628;
      const y = 145 - ((capacity - 1750) / 600) * 112;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return { hours, points: points.join(" ") };
  }),
);

const preResampleDots = Array.from({ length: 18 }, (_, index) => ({
  x: 42 + index * 35,
  y: 80 + Math.sin(index * 1.7) * 34,
  score: Math.max(0.16, 1 - Math.abs(index - 10) / 8),
}));
const postResampleDots = Array.from({ length: 18 }, (_, index) => {
  const outside = index >= 15;
  const radius = outside ? 82 + (index - 15) * 5 : 18 + (index % 5) * 9;
  const angle = index * 2.399;
  return {
    x: 390 + Math.cos(angle) * radius,
    y: 82 + Math.sin(angle) * radius,
    score: outside ? 0.58 : 0.72 + (index % 4) * 0.08,
  };
});
const resampleDots = computed(() =>
  (resampleStage.value === 2 ? postResampleDots : preResampleDots).map(
    (dot, index) => ({
      ...dot,
      id: index,
    }),
  ),
);
const resampleStatus = computed(() => {
  if (resampleStage.value === 1) {
    return "先圈出高可信粒子所在的大致区域";
  }
  if (resampleStage.value === 2) {
    return "补回的粒子集中在可信区域，也保留少量外部探索";
  }
  return "有些候选已几乎无法解释数据";
});

const remainingShare = computed(() =>
  Math.max(0, contractShare.value - attributedShare.value),
);
const recommendedBalance = computed(
  () => (remainingShare.value * estimatedCapacity.value) / 100,
);
const balanceRange = computed(() => ({
  low: recommendedBalance.value * 0.9,
  high: recommendedBalance.value * 1.1,
}));

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function stopPlayback() {
  playing.value = false;
  previousFrameTime = 0;
  cancelAnimationFrame(animationFrame);
}

function playbackFrame(time: number) {
  if (!playing.value) return;
  if (!previousFrameTime) previousFrameTime = time;
  const elapsed = time - previousFrameTime;
  previousFrameTime = time;
  playhead.value = Math.min(
    observations.length - 1,
    playhead.value + elapsed / 1500,
  );
  if (playhead.value >= observations.length - 1) {
    stopPlayback();
    return;
  }
  animationFrame = requestAnimationFrame(playbackFrame);
}

function togglePlayback() {
  if (playing.value) {
    stopPlayback();
    return;
  }
  if (playhead.value >= observations.length - 1) playhead.value = 0;
  playing.value = true;
  previousFrameTime = 0;
  animationFrame = requestAnimationFrame(playbackFrame);
}

function resetPlayback() {
  stopPlayback();
  playhead.value = 0;
}

function handlePlayheadInput() {
  stopPlayback();
}
function clearResampleTimers() {
  for (const timer of resampleTimers) window.clearTimeout(timer);
  resampleTimers = [];
}

function beginResampling() {
  resampleStage.value = 1;
  if (reducedMotion.value) {
    resampleStage.value = 2;
    return;
  }
  resampleTimers.push(
    window.setTimeout(() => {
      resampleStage.value = 2;
    }, 900),
  );
}

function runResampleDemo() {
  if (resampleStage.value === 1) return;
  clearResampleTimers();
  if (resampleStage.value === 2) {
    resampleStage.value = 0;
    return;
  }
  beginResampling();
}

function syncReducedMotion(event: MediaQueryList | MediaQueryListEvent) {
  reducedMotion.value = event.matches;
  if (event.matches) stopPlayback();
}

onMounted(() => {
  motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  syncReducedMotion(motionQuery);
  motionQuery.addEventListener("change", syncReducedMotion);
});

onBeforeUnmount(() => {
  stopPlayback();
  clearResampleTimers();
  motionQuery?.removeEventListener("change", syncReducedMotion);
});
</script>

<template>
  <div class="algorithm-lesson divide-y divide-base-300">
    <section class="lesson-section">
      <div class="lesson-copy">
        <p>
          这个问题有两种精度完全不同的数据：美元用量精确到分，周限却只显示一个整数百分比。更麻烦的是，完整周限今天可能值
          1800 美元，明天又可能缓慢变成 2000
          美元。只拿两次读数相除，会把显示误差和真实变化混在一起。
        </p>
        <p>
          粒子滤波不急着押注一个答案。它先同时保留许多种可能的解释，再让后续观测逐步淘汰不合理的解释。下面从这一步开始。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">1</span>
        <div>
          <h3>先摆出许多可能答案</h3>
          <p>每一个点都代表一种“完整周限可能是多少”的解释。</p>
        </div>
      </div>

      <div class="demo-panel mt-6">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-xs font-medium tracking-wide opacity-55">当前证据</p>
            <p class="mt-1 font-semibold">
              {{ currentObservation.label }} · 已用
              {{ formatUsd(currentObservation.cost) }} · 显示
              {{ currentObservation.percent }}%
            </p>
          </div>
          <div class="join">
            <button
              class="btn join-item btn-sm"
              type="button"
              @click="resetPlayback"
            >
              <AppIcon name="arrow-path" class="size-4" />重置
            </button>
            <button
              class="btn join-item btn-primary btn-sm"
              type="button"
              :disabled="reducedMotion"
              @click="togglePlayback"
            >
              <AppIcon :name="playing ? 'pause' : 'play'" class="size-4" />
              {{ playing ? "暂停" : "播放" }}
            </button>
          </div>
        </div>

        <svg
          class="candidate-cloud mt-5"
          viewBox="0 0 700 190"
          role="img"
          aria-label="候选周限点云"
        >
          <defs>
            <filter
              id="lesson-particle-glow"
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
            <linearGradient id="lesson-axis-gradient" x1="0" x2="1">
              <stop offset="0" stop-color="currentColor" stop-opacity="0.08" />
              <stop
                offset="0.5"
                stop-color="currentColor"
                stop-opacity="0.32"
              />
              <stop offset="1" stop-color="currentColor" stop-opacity="0.08" />
            </linearGradient>
          </defs>
          <line
            x1="38"
            y1="154"
            x2="662"
            y2="154"
            stroke="url(#lesson-axis-gradient)"
            stroke-width="2"
          />
          <g class="fill-current text-[11px] opacity-45">
            <text x="38" y="176">$1400</text>
            <text x="326" y="176" text-anchor="middle">$2700</text>
            <text x="662" y="176" text-anchor="end">$4000</text>
          </g>
          <circle
            v-for="candidate in scoredCandidates"
            :key="candidate.capacity"
            class="candidate-dot"
            :class="{ 'candidate-dot--likely': candidate.score > 0.72 }"
            :cx="candidate.x"
            :cy="candidate.y"
            :r="candidate.radius"
            :opacity="candidate.opacity"
          />
          <g v-if="playhead > 0" class="estimate-marker">
            <line
              :x1="42 + ((candidateConclusion - 1400) / 2600) * 616"
              y1="22"
              :x2="42 + ((candidateConclusion - 1400) / 2600) * 616"
              y2="154"
            />
            <text
              :x="42 + ((candidateConclusion - 1400) / 2600) * 616"
              y="15"
              text-anchor="middle"
            >
              目前约 {{ formatUsd(candidateConclusion) }}
            </text>
          </g>
        </svg>

        <input
          v-model.number="playhead"
          type="range"
          min="0"
          :max="observations.length - 1"
          step="0.01"
          class="range mt-2 w-full range-primary range-sm"
          aria-label="拖动观测进度"
          @input="handlePlayheadInput"
        />
        <div class="mt-2 flex justify-between text-xs opacity-50">
          <span>没有证据</span><span>更多观测</span>
        </div>
      </div>

      <div class="watch-note">
        <AppIcon name="cursor-arrow-rays" class="size-5 shrink-0" />
        <p>
          拖动进度条时，请看点的亮度：容量会连续变化，所以原先变暗的区域在后续观测中也可能重新亮起。暗下来表示“当前证据不支持”，不是被永久删除；这里的每一个候选点，就是“粒子”。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">2</span>
        <div>
          <h3>不猜整数是怎样显示出来的</h3>
          <p>真实进度有小数，但页面只给出整数。算法会同时保留几种显示规则。</p>
        </div>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          拖动滑块代表改变上游内部的真实进度。右侧会同时展示三种候选规则最终会在页面上显示什么整数；点选某张卡片，只是方便单独观察它，实际模型会把三种规则一起保留。
        </p>
      </div>

      <div class="demo-grid mt-6">
        <div class="demo-panel">
          <label class="text-sm font-medium" for="hidden-percent">
            隐藏的真实进度
          </label>
          <div class="mt-3 flex items-end justify-between gap-4">
            <strong class="text-3xl tabular-nums">
              {{ hiddenPercent.toFixed(1) }}%
            </strong>
            <span class="text-sm opacity-55">上游内部值</span>
          </div>
          <input
            id="hidden-percent"
            v-model.number="hiddenPercent"
            type="range"
            min="0"
            max="100"
            step="0.1"
            class="range mt-5 w-full range-primary range-sm"
          />
          <div class="mt-2 flex justify-between text-xs opacity-45">
            <span>0%</span><span>50%</span><span>100%</span>
          </div>
        </div>

        <div class="demo-panel">
          <p class="text-sm font-medium">三种规则各会怎样显示</p>
          <div class="mt-3 grid gap-2 sm:grid-cols-3">
            <button
              v-for="rule in displayRuleResults"
              :key="rule.id"
              class="rounded-box border p-3 text-left transition-colors"
              :class="
                displayRule === rule.id
                  ? 'border-primary bg-primary/10'
                  : 'border-base-300 bg-base-200 hover:border-primary/45'
              "
              type="button"
              @click="displayRule = rule.id"
            >
              <span class="block text-xs opacity-55">{{ rule.label }}</span>
              <strong class="mt-1 block text-2xl tabular-nums">
                {{ rule.value }}%
              </strong>
            </button>
          </div>
          <div
            class="mt-4 flex items-center justify-between rounded-box bg-base-300 p-4"
          >
            <span class="text-sm opacity-60">当前选中规则的页面显示</span>
            <strong class="text-4xl tabular-nums">
              {{ displayedPercent }}%
            </strong>
          </div>
        </div>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          如果我们事先武断地认定一种规则，就可能把不到 1%
          的显示误差放大成很大的美元差异。实际模型让粒子分别携带这些规则，哪一种长期更符合观测，哪一种就会获得更高的可信程度。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">3</span>
        <div>
          <h3>答案是一条路径，不是一个固定数字</h3>
          <p>完整周限会连续变化，因此粒子还要描述“它变化得有多快”。</p>
        </div>
      </div>

      <div class="demo-panel mt-6">
        <div class="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p class="text-sm font-medium">
              假设真实容量从 $1800 缓慢走向 $2300
            </p>
            <p class="mt-1 text-xs opacity-55">
              选择候选路径对新变化的跟随速度
            </p>
          </div>
          <div class="join">
            <button
              v-for="hours in [6, 24, 72] as const"
              :key="hours"
              class="btn join-item btn-sm"
              :class="selectedSpeed === hours ? 'btn-primary' : 'btn-ghost'"
              type="button"
              @click="selectedSpeed = hours"
            >
              {{ hours }} 小时
            </button>
          </div>
        </div>

        <svg
          class="speed-chart mt-5"
          viewBox="0 0 700 180"
          role="img"
          aria-label="不同响应速度的容量路径"
        >
          <g class="chart-grid">
            <line x1="36" y1="34" x2="664" y2="34" />
            <line x1="36" y1="89" x2="664" y2="89" />
            <line x1="36" y1="145" x2="664" y2="145" />
          </g>
          <g class="fill-current text-[11px] opacity-45">
            <text x="32" y="38" text-anchor="end">$2300</text>
            <text x="32" y="149" text-anchor="end">$1800</text>
            <text x="36" y="170">现在</text>
            <text x="664" y="170" text-anchor="end">72 小时后</text>
          </g>
          <polyline
            v-for="path in speedPaths"
            :key="path.hours"
            class="speed-path"
            :class="{ 'speed-path--active': selectedSpeed === path.hours }"
            :points="path.points"
          />
          <circle cx="36" cy="136" r="5" class="fill-primary" />
        </svg>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          6 小时路径会很快追随新变化，72
          小时路径更愿意相信长期趋势。系统不会提前选死一种速度；不同速度也由粒子一起竞争。这样既能跟上真正的变化，又不至于被一次整数跳动带跑。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">4</span>
        <div>
          <h3>留下可靠解释，再补回点云</h3>
          <p>当少数粒子明显更可信时，系统会围绕它们重新生成一组候选。</p>
        </div>
      </div>

      <div class="demo-panel mt-6">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="font-semibold">{{ resampleStatus }}</p>
            <p class="mt-1 text-xs opacity-55">
              点越大、越亮，表示它与已有观测越一致。
            </p>
          </div>
          <button
            class="btn btn-primary btn-sm"
            type="button"
            :disabled="resampleStage === 1"
            @click="runResampleDemo"
          >
            <span
              v-if="resampleStage === 1"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon
              v-else
              :name="
                resampleStage === 2
                  ? 'arrow-uturn-left'
                  : 'arrow-path-rounded-square'
              "
              class="size-4"
            />
            {{ resampleStage === 2 ? "回到筛选前" : "执行一次筛选" }}
          </button>
        </div>
        <svg
          class="resample-cloud mt-4"
          viewBox="0 0 700 170"
          role="img"
          aria-label="粒子筛选和重新采样"
        >
          <line
            x1="32"
            y1="145"
            x2="668"
            y2="145"
            class="stroke-base-content/15"
          />
          <circle
            v-if="resampleStage >= 1"
            class="resample-region-ring"
            cx="390"
            cy="82"
            r="70"
          />
          <text
            v-if="resampleStage >= 1"
            class="resample-region-label"
            x="478"
            y="30"
            text-anchor="start"
          >
            高可信区域（大致范围）
          </text>
          <circle
            v-for="dot in resampleDots"
            :key="dot.id"
            class="resample-dot"
            :cx="dot.x"
            :cy="dot.y"
            :r="3 + dot.score * 7"
            :opacity="0.15 + dot.score * 0.85"
          />
        </svg>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          这一步的专业名称叫“重采样”。它不是把答案强行改成平均数，而是把有限的计算量重新投入更有希望的区域。模型仍会保留一定的分散度，避免过早认定唯一答案。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="lesson-heading">
        <span class="step-number">5</span>
        <div>
          <h3>把容量路径换算成参与者余额</h3>
          <p>容量只是中间结果。最后还要扣掉已经归属给该参与者的周限。</p>
        </div>
      </div>

      <div class="demo-grid mt-6">
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
          <label class="block">
            <span class="flex justify-between text-sm"
              ><span>当前容量估计</span
              ><strong>{{ formatUsd(estimatedCapacity) }}</strong></span
            >
            <input
              v-model.number="estimatedCapacity"
              type="range"
              min="1400"
              max="4000"
              step="20"
              class="range mt-2 w-full range-secondary range-sm"
            />
          </label>
        </div>

        <div class="result-panel">
          <p class="text-xs font-medium tracking-wide opacity-55">建议余额</p>
          <strong class="mt-2 block text-4xl text-primary tabular-nums">{{
            formatUsd(recommendedBalance)
          }}</strong>
          <div class="my-5 h-px bg-base-300"></div>
          <p class="formula-line">
            ({{ contractShare }}% − {{ attributedShare }}%) ×
            {{ formatUsd(estimatedCapacity) }}
          </p>
          <p class="mt-3 text-sm opacity-65">
            示例可信范围：{{ formatUsd(balanceRange.low) }} ～
            {{ formatUsd(balanceRange.high) }}
          </p>
        </div>
      </div>

      <div class="lesson-copy mt-5">
        <p>
          真实系统不是只算上面这个单点。它会让每个粒子分别完成换算，再从所有结果中取中间结论和
          90% 可信范围；最后还会用确定性边界阻止不可能的余额。
        </p>
      </div>
    </section>

    <section class="lesson-section">
      <div class="rounded-box border border-primary/25 bg-primary/5 p-5 sm:p-6">
        <div class="flex items-start gap-4">
          <AppIcon
            name="sparkles"
            class="mt-0.5 size-6 shrink-0 text-primary"
          />
          <div>
            <h3 class="text-lg font-semibold">把五步连起来</h3>
            <p class="mt-3 max-w-4xl text-sm leading-7 opacity-75">
              系统先生成许多容量路径、显示规则和响应速度不同的粒子；每次观测后更新它们的可信程度；必要时重采样；最后把每条仍可能成立的路径换算为参与者余额。粒子轨迹页面展示的蓝色路径、90%
              区间和点云，就是这段过程的可视化结果。
            </p>
            <RouterLink
              to="/particle-filter"
              class="btn mt-5 btn-primary btn-sm"
            >
              打开真实粒子轨迹<AppIcon
                name="arrow-trending-up"
                class="size-4"
              />
            </RouterLink>
          </div>
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

.demo-grid {
  display: grid;
  gap: 1rem;
}

.candidate-cloud,
.speed-chart,
.resample-cloud {
  display: block;
  width: 100%;
  overflow: visible;
}

.candidate-dot {
  fill: color-mix(in oklab, var(--color-primary) 72%, var(--color-secondary));
  transform-box: fill-box;
  transform-origin: center;
  transition:
    r 420ms ease,
    opacity 420ms ease,
    fill 420ms ease;
}

.candidate-dot--likely {
  fill: var(--color-primary);
  filter: url(#lesson-particle-glow);
  animation: lesson-particle-breathe 1.9s ease-in-out infinite alternate;
}

.estimate-marker line {
  stroke: var(--color-primary);
  stroke-width: 1.5;
  stroke-dasharray: 4 5;
  opacity: 0.65;
}

.estimate-marker text {
  fill: currentColor;
  font-size: 11px;
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
  color: color-mix(in oklab, currentColor 82%, transparent);
  font-size: 0.85rem;
  line-height: 1.55rem;
}

.chart-grid line {
  stroke: currentColor;
  stroke-width: 1;
  stroke-dasharray: 3 7;
  opacity: 0.12;
}

.speed-path {
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  opacity: 0.12;
  transition:
    opacity 240ms ease,
    stroke-width 240ms ease;
}

.speed-path--active {
  stroke: var(--color-primary);
  stroke-width: 5;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 1;
  stroke-dasharray: 10 8;
  animation: lesson-flow 1.1s linear infinite;
}

.resample-dot {
  fill: var(--color-secondary);
  filter: drop-shadow(
    0 0 6px color-mix(in oklab, var(--color-secondary) 52%, transparent)
  );
  transition:
    cx 520ms cubic-bezier(0.22, 1, 0.36, 1),
    cy 520ms cubic-bezier(0.22, 1, 0.36, 1),
    r 360ms ease,
    opacity 360ms ease;
}
.resample-region-ring {
  fill: color-mix(in oklab, var(--color-primary) 7%, transparent);
  stroke: var(--color-primary);
  stroke-width: 2.5;
  stroke-dasharray: 440;
  stroke-dashoffset: 440;
  filter: drop-shadow(
    0 0 8px color-mix(in oklab, var(--color-primary) 38%, transparent)
  );
  animation: resample-ring-draw 700ms ease forwards;
}

.resample-region-label {
  fill: currentColor;
  font-size: 11px;
  font-weight: 700;
  opacity: 0.62;
}

.result-panel {
  display: flex;
  min-height: 15rem;
  flex-direction: column;
  justify-content: center;
  text-align: center;
}

.formula-line {
  font-size: clamp(1rem, 2.1vw, 1.25rem);
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

@keyframes lesson-particle-breathe {
  from {
    transform: scale(0.88);
  }
  to {
    transform: scale(1.14);
  }
}

@keyframes lesson-flow {
  to {
    stroke-dashoffset: -18;
  }
}
@keyframes resample-ring-draw {
  to {
    stroke-dashoffset: 0;
  }
}

@media (min-width: 48rem) {
  .demo-panel,
  .result-panel {
    padding: 1.35rem;
  }

  .demo-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (prefers-reduced-motion: reduce) {
  .candidate-dot,
  .speed-path,
  .resample-dot {
    transition-duration: 0.01ms;
  }

  .candidate-dot--likely,
  .speed-path--active,
  .resample-region-ring {
    animation: none;
    stroke-dashoffset: 0;
  }
}
</style>
