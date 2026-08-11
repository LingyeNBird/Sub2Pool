<script setup lang="ts">
import { computed } from "vue";

import type {
  HistoricalCoverageDimension,
  HistoricalCoverageStatus,
  HistoricalRebuildMode,
  HistoricalRebuildPlan,
} from "@/types";

const { plan, planning, applying, rollingBack } = defineProps<{
  plan: HistoricalRebuildPlan | null;
  planning: HistoricalRebuildMode | "";
  applying: boolean;
  rollingBack: boolean;
}>();

const emit = defineEmits<{
  createPlan: [mode: HistoricalRebuildMode];
  apply: [];
  rollback: [];
}>();

const busy = computed(() => Boolean(planning || applying || rollingBack));
const hardBlockers = computed(
  () => plan?.blockers.filter((item) => item.severity === "hard") ?? [],
);
const warnings = computed(
  () => plan?.blockers.filter((item) => item.severity === "warning") ?? [],
);

const dimensionLabels: Record<HistoricalCoverageDimension, string> = {
  account_cost: "账号成本",
  user_cost: "逐用户成本",
  fast_cost: "FAST / service tier",
  request_count: "请求数",
  api_key: "API Key 构成",
};

const statusLabels: Record<HistoricalCoverageStatus, string> = {
  verified: "已验证",
  verified_empty: "已验证为空",
  captured_local: "本地完整写入",
  out_of_scope: "目标范围外",
  policy_only: "仅策略推定",
  unknown: "未知",
  unavailable: "不可用",
};

const coverageSummary = computed(() => {
  const dimensions = Object.keys(
    dimensionLabels,
  ) as HistoricalCoverageDimension[];
  return dimensions.map((dimension) => {
    const rows =
      plan?.coverage.filter((row) => row.dimension === dimension) ?? [];
    const counts = new Map<HistoricalCoverageStatus, number>();
    for (const row of rows) {
      counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
    }
    return {
      dimension,
      rows,
      counts: [...counts.entries()],
    };
  });
});

function stateLabel(state: HistoricalRebuildPlan["state"]) {
  return {
    generating: "生成中",
    ready: "可安全应用",
    blocked: "已阻断",
    stale: "已过期",
    applying: "应用中",
    applied: "已应用",
    rolled_back: "已回滚",
    failed: "失败",
  }[state];
}

function statusClass(status: HistoricalCoverageStatus) {
  if (
    status === "verified" ||
    status === "verified_empty" ||
    status === "captured_local"
  ) {
    return "badge-success";
  }
  if (
    status === "policy_only" ||
    status === "unknown" ||
    status === "out_of_scope"
  ) {
    return "badge-warning";
  }
  return "badge-ghost";
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
          先生成不可变计划，再按 plan id 与 digest
          应用。应用阶段零联网；coverage
          不足时只展示诊断，不提供绕过验证的强制入口。
        </p>
      </div>

      <div class="grid gap-3">
        <section class="rounded-box bg-base-100 p-4">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="font-semibold">本地审计并重放</h3>
              <p class="mt-1 text-xs leading-5 opacity-60">
                审计所有观测与非观测采样点、用户集合、窗口、残差和历史 FAST
                明细。创建计划和应用均不会连接 Sub2API。
              </p>
            </div>
            <span class="badge shrink-0 badge-outline badge-success">推荐</span>
          </div>
          <button
            class="btn mt-3 btn-sm"
            :disabled="busy"
            @click="emit('createPlan', 'audit_replay')"
          >
            <span
              v-if="planning === 'audit_replay'"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="document-magnifying-glass" class="size-4" />
            创建本地计划
          </button>
        </section>

        <section class="rounded-box border border-warning/30 bg-base-100 p-4">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 class="font-semibold">远端验证修复</h3>
              <p class="mt-1 text-xs leading-5 opacity-60">
                分块读取候选日志，但只有账号、用户与 FAST
                各自获得独立覆盖证明时才生成 typed
                patch。分页完整和查询天数本身不构成证明。
              </p>
            </div>
            <span class="badge shrink-0 badge-outline badge-warning"
              >严格 coverage</span
            >
          </div>
          <button
            class="btn mt-3 btn-sm"
            :disabled="busy"
            @click="emit('createPlan', 'verified_remote_repair')"
          >
            <span
              v-if="planning === 'verified_remote_repair'"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="document-magnifying-glass" class="size-4" />
            创建远端候选计划
          </button>
        </section>
      </div>

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
                'badge-ghost': plan.state === 'rolled_back',
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
            <div>
              <dt class="opacity-60">Digest</dt>
              <dd class="font-mono text-xs break-all">{{ plan.digest }}</dd>
            </div>
            <div>
              <dt class="opacity-60">Typed patch</dt>
              <dd>
                共 {{ plan.patch_summary.total }} 个：账号
                {{ plan.patch_summary.observation_cost }}、用户
                {{ plan.patch_summary.user_cost }}、FAST
                {{ plan.patch_summary.fast_fact }}
              </dd>
            </div>
          </dl>
        </div>

        <div class="space-y-2">
          <h3 class="text-sm font-semibold">逐维 coverage</h3>
          <div
            v-for="item in coverageSummary"
            :key="item.dimension"
            class="flex flex-wrap items-center justify-between gap-2 rounded-box bg-base-100 p-3 text-sm"
          >
            <span class="font-medium">{{
              dimensionLabels[item.dimension]
            }}</span>
            <div class="flex flex-wrap gap-1">
              <span
                v-for="[status, count] in item.counts"
                :key="status"
                class="badge badge-outline badge-sm"
                :class="statusClass(status)"
              >
                {{ statusLabels[status] }} × {{ count }}
              </span>
              <span v-if="!item.rows.length" class="badge badge-ghost badge-sm">
                无范围
              </span>
            </div>
          </div>
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
            {{ warnings.length }} 项旧数据或非阻断不确定性仍会保留在审计记录中。
          </span>
        </div>
        <div v-if="plan.unknown_coverage" class="alert text-sm">
          <AppIcon name="information-circle" class="size-5" />
          <span>
            存在 unknown / policy-only / unavailable
            维度；这不等于“全部历史已经修复”。
          </span>
        </div>

        <div class="flex flex-wrap gap-2">
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
          <button
            class="btn btn-sm btn-warning"
            :disabled="!plan.can_rollback || busy"
            @click="emit('rollback')"
          >
            <span
              v-if="rollingBack"
              class="loading loading-xs loading-spinner"
            ></span>
            <AppIcon v-else name="arrow-uturn-left" class="size-4" />
            业务回滚
          </button>
        </div>
        <p class="text-xs leading-5 opacity-60">
          回滚只恢复本次 touched source before-image 并用同版本算法重放；cutoff
          后新增采样保留，不承诺字节级数据库恢复。
        </p>
      </section>
    </div>
  </section>
</template>
