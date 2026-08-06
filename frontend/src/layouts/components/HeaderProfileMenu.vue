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
  <div class="z-10">
    <button
      class="btn avatar btn-circle btn-ghost btn-sm"
      aria-label="打开账户菜单"
      popovertarget="header-profile-dropdown"
      style="anchor-name: --header-profile-anchor"
    >
      <div
        class="flex size-7 items-center justify-center rounded-full bg-neutral text-xs text-neutral-content"
      >
        {{ auth.username.slice(0, 1).toUpperCase() }}
      </div>
    </button>
    <ul
      id="header-profile-dropdown"
      class="menu dropdown mt-3 w-52 rounded-box bg-base-100 p-2 shadow-2xl"
      popover
      style="position-anchor: --header-profile-anchor"
    >
      <li class="menu-title">{{ auth.username }}</li>
      <li v-if="auth.isStaff">
        <RouterLink to="/settings">系统设置</RouterLink>
      </li>
      <li><button type="button" @click="logout">退出登录</button></li>
    </ul>
  </div>
</template>
