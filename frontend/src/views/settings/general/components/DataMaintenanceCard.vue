<script setup lang="ts">
import { computed } from "vue";

import type { HistoricalRebuildPlan } from "@/types";

const { plan, planning, applying } = defineProps<{
  plan: HistoricalRebuildPlan | null;
  planning: boolean;
  applying: boolean;
}>();

const emit = defineEmits<{
  createPlan: [];
  apply: [];
}>();

const busy = computed(() => planning || applying);
const hardBlockers = computed(
  () => plan?.blockers.filter((item) => item.severity === "hard") ?? [],
);
const warnings = computed(
  () => plan?.blockers.filter((item) => item.severity === "warning") ?? [],
);

function stateLabel(state: HistoricalRebuildPlan["state"]) {
  return {
    generating: "生成中",
    ready: "可安全应用",
    blocked: "已阻断",
    stale: "已过期",
    applying: "应用中",
    applied: "已应用",
    failed: "失败",
  }[state];
}
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-5">
      <div>
        <h2 class="card-title">
          <AppIcon name="wrench-screwdriver" class="size-5" />数据维护
        </h2>
        <p class="mt-2 text-sm leading-6 opacity-70">
          先冻结本地全点审计计划，再按 plan id 与 digest
          确定性重放派生结果。创建和应用都不会连接
          Sub2API，也不会用请求日志改写历史来源事实。
        </p>
      </div>

      <section class="rounded-box bg-base-100 p-4">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h3 class="font-semibold">本地审计并重放</h3>
            <p class="mt-1 text-xs leading-5 opacity-60">
              审计所有观测与非观测采样点、用户集合、窗口、残差和历史 FAST
              明细。存在硬阻断时不会应用；通过后只重建可派生结果。
            </p>
          </div>
          <span class="badge shrink-0 badge-outline badge-success">推荐</span>
        </div>
        <button
          class="btn mt-3 btn-sm"
          :disabled="busy"
          @click="emit('createPlan')"
        >
          <span
            v-if="planning"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="document-magnifying-glass" class="size-4" />
          创建本地计划
        </button>
      </section>

      <section v-if="plan" class="space-y-4">
        <div class="rounded-box bg-base-100 p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="font-semibold">计划 {{ plan.id }}</h3>
            <span
              class="badge"
              :class="{
                'badge-success':
                  plan.state === 'ready' || plan.state === 'applied',
                'badge-warning':
                  plan.state === 'blocked' || plan.state === 'stale',
              }"
            >
              {{ stateLabel(plan.state) }}
            </span>
          </div>
          <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt class="opacity-60">事实 revision</dt>
              <dd class="font-mono">
                {{ plan.base_revision }} → {{ plan.result_revision ?? "—" }}
              </dd>
            </div>
            <div>
              <dt class="opacity-60">计划过期时间</dt>
              <dd>{{ new Date(plan.expires_at).toLocaleString() }}</dd>
            </div>
            <div class="sm:col-span-2">
              <dt class="opacity-60">Digest</dt>
              <dd class="font-mono text-xs break-all">{{ plan.digest }}</dd>
            </div>
            <div
              v-if="plan.replay_summary.rebuilt_observations !== undefined"
              class="sm:col-span-2"
            >
              <dt class="opacity-60">重放结果</dt>
              <dd>
                重建
                {{ plan.replay_summary.rebuilt_observations }} 条观测，自动排除
                {{ plan.replay_summary.automatic_exclusions ?? 0 }} 条，推断区间
                {{ plan.replay_summary.inferred_intervals ?? 0 }} 个。
              </dd>
            </div>
          </dl>
        </div>

        <div v-if="hardBlockers.length" class="alert items-start alert-error">
          <AppIcon name="exclamation-triangle" class="mt-0.5 size-5" />
          <div>
            <div class="font-semibold">计划被硬阻断</div>
            <ul class="mt-1 list-disc space-y-1 pl-5 text-sm">
              <li
                v-for="item in hardBlockers"
                :key="`${item.code}-${item.point_id}`"
              >
                {{ item.message }}
                <span v-if="item.point_id">（point {{ item.point_id }}）</span>
              </li>
            </ul>
          </div>
        </div>
        <div v-if="warnings.length" class="alert items-start alert-warning">
          <AppIcon name="information-circle" class="mt-0.5 size-5" />
          <span class="text-sm">
            {{ warnings.length }} 项非阻断问题已记录；应用不会据此改写来源事实。
          </span>
        </div>

        <button
          class="btn btn-primary btn-sm"
          :disabled="!plan.safe_to_apply || busy"
          @click="emit('apply')"
        >
          <span
            v-if="applying"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="arrow-path" class="size-4" />
          应用冻结计划
        </button>
      </section>
    </div>
  </section>
</template>
