<script setup lang="ts">
defineProps<{
  successCount: number;
  failureCount: number;
  uniqueRequestIps: number;
}>();
</script>

<template>
  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">成功登录</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ successCount }}
          </div>
          <div class="stat-desc">包含管理员和普通用户登录</div>
        </div>
        <AppIcon name="shield-check" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">失败尝试</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ failureCount }}
          </div>
          <div class="stat-desc">密码错误、无权限或被限流</div>
        </div>
        <AppIcon name="shield-exclamation" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">服务端来源 IP</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ uniqueRequestIps }}
          </div>
          <div class="stat-desc">按可信代理配置解析</div>
        </div>
        <AppIcon name="globe-alt" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
  </section>
</template>
