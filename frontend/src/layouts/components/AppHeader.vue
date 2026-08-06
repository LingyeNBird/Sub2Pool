<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import { useThemeStore } from "@/stores/theme";

import HeaderNotifications from "./HeaderNotifications.vue";
import HeaderProfileMenu from "./HeaderProfileMenu.vue";
import HeaderThemeMenu from "./HeaderThemeMenu.vue";

const route = useRoute();
const theme = useThemeStore();
const pageTitle = computed(() => String(route.meta.title ?? "Dashboard"));
</script>

<template>
  <header class="col-span-12 flex items-center gap-1">
    <label
      for="my-drawer"
      class="btn btn-square btn-ghost drawer-button lg:hidden"
      aria-label="Open sidebar"
    >
      <AppIcon name="bars-3" class="h-5 w-5" />
    </label>
    <div class="grow">
      <h1 class="page-title opacity-50 lg:text-xl lg:font-light">
        {{ pageTitle }}
      </h1>
    </div>
    <label
      class="btn swap btn-circle swap-rotate btn-ghost btn-sm"
      aria-label="Toggle theme"
    >
      <input type="checkbox" :checked="theme.isLight" @change="theme.toggle" />
      <svg
        class="size-4 swap-on fill-current"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
      >
        <path
          d="M5.64 17l-.71.71a1 1 0 001.41 1l.71-.71A1 1 0 005.64 17zM5 12a1 1 0 00-1-1H3a1 1 0 000 2h1a1 1 0 001-1zm7-7a1 1 0 001-1V3a1 1 0 00-2 0v1a1 1 0 001 1zM5.64 7.05a1 1 0 001.41-1.41l-.71-.71a1 1 0 00-1.41 1.41l.71.71zM21 11h-1a1 1 0 000 2h1a1 1 0 000-2zm-9 8a1 1 0 00-1 1v1a1 1 0 002 0v-1a1 1 0 00-1-1zm6.36-2A1 1 0 0017 18.36l.71.71a1 1 0 001.41-1.41l-.76-.66zM12 6.5a5.5 5.5 0 105.5 5.5A5.51 5.51 0 0012 6.5zm0 9a3.5 3.5 0 113.5-3.5 3.5 3.5 0 01-3.5 3.5z"
        />
      </svg>
      <svg
        class="size-4 swap-off fill-current"
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
      >
        <path
          d="M21.64 13a1 1 0 00-1.05-.14 8.05 8.05 0 01-3.37.73 8.15 8.15 0 01-8.14-8.1 8.59 8.59 0 01.25-2A1 1 0 008 2.36 10.14 10.14 0 1022 14.05 1 1 0 0021.64 13zm-9.5 6.69A8.14 8.14 0 017.08 5.22v.27a10.15 10.15 0 0010.14 10.14 9.79 9.79 0 002.1-.22 8.11 8.11 0 01-7.18 4.32z"
        />
      </svg>
    </label>
    <HeaderThemeMenu />
    <HeaderNotifications />
    <HeaderProfileMenu />
  </header>
</template>
