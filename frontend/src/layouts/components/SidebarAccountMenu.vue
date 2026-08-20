<script setup lang="ts">
import { useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();

async function logout() {
  await auth.signOut();
  await router.replace("/login");
}
</script>

<template>
  <div
    class="sticky bottom-0 mt-auto border-t border-base-content/5 bg-base-200 py-2"
  >
    <button
      class="btn h-auto w-full justify-start btn-ghost px-3 py-2"
      aria-label="打开账户菜单"
      popovertarget="sidebar-account-dropdown"
      style="anchor-name: --sidebar-account-anchor"
    >
      <div class="avatar">
        <div
          class="flex w-9 items-center justify-center rounded-full bg-neutral text-sm text-neutral-content"
        >
          {{ auth.username.slice(0, 1).toUpperCase() }}
        </div>
      </div>
      <span class="grid min-w-0 grow text-left">
        <span class="truncate font-semibold">{{ auth.username }}</span>
        <span class="truncate text-xs font-normal opacity-60">
          {{ auth.isStaff ? "管理员" : "普通用户" }}
        </span>
      </span>
      <AppIcon name="chevron-up" class="size-4" />
    </button>
    <ul
      id="sidebar-account-dropdown"
      class="menu dropdown dropdown-top z-20 mb-2 w-60 rounded-box bg-base-100 p-2 shadow-2xl"
      popover
      style="position-anchor: --sidebar-account-anchor"
    >
      <li v-if="auth.canAccess('settings')">
        <RouterLink to="/settings">
          <AppIcon name="cog-6-tooth" class="size-4" />
          系统设置
        </RouterLink>
      </li>
      <li>
        <a
          href="https://github.com/LingyeNBird/Sub2Pool"
          target="_blank"
          rel="noreferrer"
        >
          <AppIcon name="code-bracket" class="size-4" />
          开源代码
        </a>
      </li>
      <li>
        <button type="button" @click="logout">
          <AppIcon name="arrow-right-on-rectangle" class="size-4" />
          退出登录
        </button>
      </li>
    </ul>
  </div>
</template>
