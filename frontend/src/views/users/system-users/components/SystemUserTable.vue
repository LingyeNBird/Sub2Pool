<script setup lang="ts">
import { useDateTime } from "@/composables/useDateTime";
import type { SystemUser } from "@/types";

defineProps<{
  users: SystemUser[];
  loading: boolean;
  editable: boolean;
}>();

defineEmits<{
  edit: [user: SystemUser];
  editPermissions: [user: SystemUser];
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
          页面权限控制可进入的功能；账号和参与者权限限制对应页面中的可见数据。
        </p>
      </div>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else-if="users.length" class="overflow-x-auto">
        <table class="table min-w-[52rem]">
          <thead>
            <tr>
              <th>用户</th>
              <th>页面权限</th>
              <th>可见参与者</th>
              <th>可见账号</th>
              <th>状态</th>
              <th>最近登录</th>
              <th v-if="editable"></th>
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
                <span class="badge badge-sm badge-primary">
                  {{ user.page_permissions.length }} 个页面
                </span>
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
                  <span
                    v-if="!user.participant_names.length"
                    class="text-sm opacity-50"
                  >
                    未选择
                  </span>
                </div>
              </td>
              <td>
                <div class="flex max-w-lg flex-wrap gap-1">
                  <span
                    v-for="(name, index) in user.account_names"
                    :key="`${user.id}-account-${index}`"
                    class="badge badge-ghost badge-sm"
                  >
                    {{ name }}
                  </span>
                  <span
                    v-if="!user.account_names.length"
                    class="text-sm opacity-50"
                  >
                    未选择
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
              <td v-if="editable" class="text-right whitespace-nowrap">
                <button
                  class="btn btn-ghost btn-xs"
                  @click="$emit('edit', user)"
                >
                  编辑
                </button>
                <button
                  class="btn btn-ghost text-primary btn-xs"
                  @click="$emit('editPermissions', user)"
                >
                  编辑权限
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
