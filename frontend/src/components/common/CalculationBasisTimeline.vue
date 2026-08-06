<script setup lang="ts">
withDefaults(
  defineProps<{
    startLabel?: string;
    startTime: string;
    startValue: string;
    endLabel?: string;
    endTime: string;
    endValue: string;
  }>(),
  {
    startLabel: "计算起点",
    endLabel: "计算终点",
  },
);
</script>

<template>
  <div class="mt-5 overflow-x-auto pb-1">
    <ul class="timeline timeline-horizontal w-full min-w-120">
      <li class="min-w-0 flex-1">
        <div class="timeline-start w-full max-w-64 timeline-box text-left">
          <div class="text-xs opacity-60">{{ startLabel }}</div>
          <div class="mt-1 font-semibold">{{ startTime }}</div>
          <div class="mt-1 tabular-nums">{{ startValue }}</div>
        </div>
        <div class="timeline-middle">
          <span
            class="block size-3 rounded-full bg-primary ring-4 ring-primary/20"
          ></span>
        </div>
        <hr class="bg-primary" />
      </li>
      <li class="min-w-0 flex-1">
        <hr class="bg-primary" />
        <div class="timeline-middle">
          <span
            class="block size-3 rounded-full bg-primary ring-4 ring-primary/20"
          ></span>
        </div>
        <div class="timeline-end w-full max-w-64 timeline-box text-left">
          <div class="text-xs opacity-60">{{ endLabel }}</div>
          <div class="mt-1 font-semibold">{{ endTime }}</div>
          <div class="mt-1 tabular-nums">{{ endValue }}</div>
        </div>
      </li>
    </ul>
  </div>
</template>
