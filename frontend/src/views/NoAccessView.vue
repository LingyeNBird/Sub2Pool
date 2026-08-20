<script setup lang="ts">
import PageShellHeader from "@/components/common/PageShellHeader.vue";
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><h1>暂无访问权限</h1></li>
        </ul>
      </div>
    </div>
  </PageShellHeader>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body items-center py-16 text-center">
      <AppIcon name="lock-closed" class="size-10 text-warning" />
      <h2 class="mt-3 text-xl font-semibold">当前账号尚未分配页面权限</h2>
      <p class="mt-1 max-w-lg text-sm opacity-60">
        请联系管理员，在“系统用户”的“编辑权限”中配置可访问页面和参与者范围。
      </p>
    </div>
  </section>
</template>
