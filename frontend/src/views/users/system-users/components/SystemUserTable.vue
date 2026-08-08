<script setup lang="ts">
import { useDateTime } from "@/composables/useDateTime";
import type { SystemUser } from "@/types";

defineProps<{
  users: SystemUser[];
  loading: boolean;
}>();

defineEmits<{
  edit: [user: SystemUser];
  remove: [user: SystemUser];
}>();

const dateTime = useDateTime("从未登录");
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <div>
        <h2 class="card-title">
          <AppIcon name="identification" class="size-5" />用户与可见范围
        </h2>
        <p class="mt-1 text-sm opacity-60">
          普通用户只能进入额度统计页面，并且只能看到绑定参与者的账号用量。
        </p>
      </div>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else-if="users.length" class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>用户</th>
              <th>绑定参与者</th>
              <th>状态</th>
              <th>最近登录</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in users" :key="user.id">
              <td>
                <div class="font-semibold">{{ user.username }}</div>
                <div class="text-sm opacity-60">
                  {{ user.email || "未填写邮箱" }}
                </div>
              </td>
              <td>
                <div class="flex max-w-lg flex-wrap gap-1">
                  <span
                    v-for="(name, index) in user.participant_names"
                    :key="`${user.id}-${index}`"
                    class="badge badge-ghost badge-sm"
                  >
                    {{ name }}
                  </span>
                </div>
              </td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="user.is_active ? 'badge-success' : 'badge-ghost'"
                >
                  {{ user.is_active ? "启用" : "停用" }}
                </span>
              </td>
              <td class="whitespace-nowrap">{{ dateTime(user.last_login) }}</td>
              <td class="text-right whitespace-nowrap">
                <button
                  class="btn btn-ghost btn-xs"
                  @click="$emit('edit', user)"
                >
                  编辑
                </button>
                <button
                  class="btn btn-ghost text-error btn-xs"
                  @click="$emit('remove', user)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="py-8 text-center opacity-60">尚未添加普通用户</div>
    </div>
  </section>
</template>
