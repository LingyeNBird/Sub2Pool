import { computed, onMounted, onUnmounted, ref } from "vue";

import { api } from "@/services/api";
import type { MonitorSchedule } from "@/types";

export function useMonitorScheduleCountdown(
  onRunCompleted: () => Promise<void> | void,
) {
  const schedule = ref<MonitorSchedule | null>(null);
  const clientNow = ref(Date.now());
  const serverOffsetMs = ref(0);
  let clockTimer: number | undefined;
  let lastScheduleRefreshAt = 0;
  let expiredScheduleAt: string | null = null;
  let refreshingSchedule = false;

  const remainingMs = computed(() => {
    if (
      !schedule.value?.monitoring_enabled ||
      !schedule.value.next_local_check_at
    )
      return 0;
    const nextAt = new Date(schedule.value.next_local_check_at).getTime();
    return Math.max(0, nextAt - (clientNow.value + serverOffsetMs.value));
  });

  const countdownProgress = computed(() => {
    const intervalMs = (schedule.value?.interval_seconds ?? 0) * 1000;
    if (!intervalMs) return 0;
    return Math.min(100, (remainingMs.value / intervalMs) * 100);
  });

  const remainingLabel = computed(() => {
    if (!schedule.value?.next_local_check_at) return "等待轮询器登记";
    const seconds = Math.max(0, Math.ceil(remainingMs.value / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    if (hours) return `${hours} 小时 ${minutes} 分 ${remainder} 秒`;
    return `${minutes} 分 ${remainder} 秒`;
  });

  function applySchedule(value: MonitorSchedule) {
    schedule.value = value;
    serverOffsetMs.value = new Date(value.server_time).getTime() - Date.now();
    lastScheduleRefreshAt = Date.now();
  }

  async function refreshSchedule() {
    if (refreshingSchedule) return;
    refreshingSchedule = true;
    try {
      const awaitedSchedule = expiredScheduleAt;
      const wasRunning = schedule.value?.run_in_progress ?? false;
      const value = await api<MonitorSchedule>("monitor/run");
      const scheduleAdvanced = Boolean(
        awaitedSchedule &&
        value.next_local_check_at &&
        new Date(value.next_local_check_at).getTime() >
          new Date(awaitedSchedule).getTime(),
      );
      applySchedule(value);
      if (!value.monitoring_enabled) expiredScheduleAt = null;
      if ((scheduleAdvanced || wasRunning) && !value.run_in_progress) {
        expiredScheduleAt = null;
        await onRunCompleted();
      }
    } catch {
      // 后台尚未登记下一时隙时继续轮询，不覆盖页面自身的加载错误。
    } finally {
      refreshingSchedule = false;
    }
  }

  function tick() {
    clientNow.value = Date.now();
    if (
      remainingMs.value === 0 &&
      schedule.value?.monitoring_enabled &&
      schedule.value.next_local_check_at &&
      !expiredScheduleAt
    ) {
      expiredScheduleAt = schedule.value.next_local_check_at;
    }
    if (
      schedule.value?.monitoring_enabled &&
      (expiredScheduleAt || schedule.value.run_in_progress) &&
      Date.now() - lastScheduleRefreshAt >= 5000
    ) {
      lastScheduleRefreshAt = Date.now();
      void refreshSchedule();
    }
  }

  onMounted(() => {
    clockTimer = window.setInterval(tick, 1000);
  });
  onUnmounted(() => window.clearInterval(clockTimer));

  return {
    schedule,
    countdownProgress,
    remainingLabel,
    applySchedule,
  };
}
