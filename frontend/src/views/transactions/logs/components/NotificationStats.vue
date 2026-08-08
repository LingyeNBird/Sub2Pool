<script setup lang="ts">
defineProps<{
  total: number;
  sentCount: number;
  failedCount: number;
}>();
</script>

<template>
  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">通知记录</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ total }}
          </div>
          <div class="stat-desc">包含发送、跳过和失败</div>
        </div>
        <AppIcon name="bell" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">已发送</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ sentCount }}
          </div>
          <div class="stat-desc">邮件服务接收成功</div>
        </div>
        <AppIcon name="check-circle" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">发送失败</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ failedCount }}
          </div>
          <div class="stat-desc">请检查邮件服务设置</div>
        </div>
        <AppIcon
          name="exclamation-triangle"
          class="size-7 shrink-0 opacity-40"
        />
      </div>
    </div>
  </section>
</template>
