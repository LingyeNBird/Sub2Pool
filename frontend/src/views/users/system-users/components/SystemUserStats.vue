<script setup lang="ts">
defineProps<{
  userCount: number;
  activeCount: number;
  bindingCount: number;
}>();
</script>

<template>
  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">普通用户</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ userCount }}
          </div>
          <div class="stat-desc">不包含管理员账号</div>
        </div>
        <AppIcon name="users" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">已启用</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ activeCount }}
          </div>
          <div class="stat-desc">停用后不能继续登录</div>
        </div>
        <AppIcon name="check-circle" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">参与者绑定</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ bindingCount }}
          </div>
          <div class="stat-desc">一个用户可以绑定多个参与者</div>
        </div>
        <AppIcon name="link" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
  </section>
</template>
