<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "@/services/api";
import { temporaryBurstState } from "@/stores/temporaryBurst";
import { useDateTime } from "@/composables/useDateTime";
import type { TemporaryBurstData } from "@/types/temporaryBurst";

const data = temporaryBurstState;
const dateTime = useDateTime();
const stale = ref(false);
const now = ref(Date.now());
const pending = computed(
  () =>
    data.value?.cycles.filter((row) => row.is_burst_cycle && !row.settled_at)
      .length ?? 0,
);
const cadence = computed(
  () => data.value?.sampling.filter((row) => row.accelerated) ?? [],
);
const countdown = computed(() => {
  if (!data.value?.expires_at) return "—";
  const seconds = Math.max(
    0,
    Math.ceil((Date.parse(data.value.expires_at) - now.value) / 1000),
  );
  return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
});
let disposed = false;
let loading = false;
let poll: ReturnType<typeof setInterval>;
let clock: ReturnType<typeof setInterval>;
async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const result = await api<TemporaryBurstData>("dashboard/temporary-burst");
    if (!disposed) {
      data.value = result;
      stale.value = false;
    }
  } catch {
    if (!disposed) stale.value = true;
  } finally {
    loading = false;
  }
}
onMounted(() => {
  void refresh();
  poll = setInterval(() => void refresh(), 30000);
  clock = setInterval(() => {
    now.value = Date.now();
    if (
      import.meta.env.VITE_DEMO_MODE !== "true" &&
      data.value?.active &&
      data.value.expires_at &&
      now.value >= Date.parse(data.value.expires_at)
    ) {
      data.value = { ...data.value, active: false };
      void refresh();
    }
  }, 1000);
});
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(poll);
  clearInterval(clock);
  data.value = null;
});
</script>

<template>
  <Transition
    enter-active-class="transition-opacity duration-200 motion-reduce:transition-none"
    enter-from-class="opacity-0"
    leave-active-class="transition-opacity duration-150 motion-reduce:transition-none"
    leave-to-class="opacity-0"
  >
    <aside
      v-if="data && (data.active || pending)"
      aria-label="爽蹬全局状态"
      class="col-span-12 flex flex-wrap items-center justify-between gap-3 rounded-box border px-4 py-3"
      :class="
        data.active
          ? 'border-orange-500/50 bg-orange-500/10'
          : 'border-base-300 bg-base-200'
      "
    >
      <div class="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <strong class="flex items-center gap-2"
          ><AppIcon name="bolt" class="size-5 text-orange-500" />{{
            data.active ? "爽蹬中" : "爽蹬已退出 · 待结算"
          }}</strong
        >
        <span class="badge badge-outline">{{
          data.carryover_enabled ? "权益结转" : "不结转"
        }}</span>
        <span v-if="data.active"
          >全局共享余额 · 涉及
          {{ data.cycles.filter((row) => row.is_burst_cycle).length }}
          个账号</span
        >
        <span v-else>{{ pending }} 个账号等待原周期结算</span>
        <span v-if="!data.monitoring_enabled" class="text-warning"
          >监控暂停 · 不会自动采样</span
        >
        <span v-else-if="cadence.length"
          >{{ cadence.length }} 个账号采样加速 ·
          {{
            [...new Set(cadence.map((row) => row.interval_seconds / 60))].join(
              " / ",
            )
          }}
          分钟一次</span
        >
        <span v-if="data.active" :title="dateTime(data.expires_at)"
          >本轮爽蹬剩余时间
          <span class="font-mono tabular-nums">{{ countdown }}</span></span
        >
        <span v-if="stale" class="text-warning"
          >状态刷新失败，显示最后记录</span
        >
      </div>
      <RouterLink to="/#temporary-burst" class="btn btn-ghost btn-sm"
        >查看状态与结算</RouterLink
      >
      <p v-if="data.active" class="w-full text-xs opacity-70">
        任一账号提前重置会提前结束本轮爽蹬。{{
          data.carryover_enabled
            ? "借用的权益会在后续周期结转。"
            : "本轮不结转，开启前需告知所有车友。"
        }}模式开启不代表余额已全部应用；使用重置卡仍建议沟通时间安排。
      </p>
    </aside>
  </Transition>
</template>
