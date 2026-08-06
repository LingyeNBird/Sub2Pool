<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { navigation, type NavigationGroup } from "@/config/navigation";

import SidebarAccountMenu from "./SidebarAccountMenu.vue";

const route = useRoute();
const menu = ref<HTMLElement | null>(null);
const sidebar = ref<HTMLElement | null>(null);
const scrollStorageKey = "dashboard:sidebar-scroll-top";
let layoutObserver: ResizeObserver | null = null;
let gutterTimer: number | undefined;

function isActive(path: string) {
  return route.path === path;
}

function isGroupActive(group: NavigationGroup) {
  if (group.to) {
    return isActive(group.to);
  }

  return Boolean(group.children?.some((item) => route.path === item.to));
}

function saveScroll() {
  if (menu.value) {
    sessionStorage.setItem(scrollStorageKey, String(menu.value.scrollTop));
  }
}

async function syncSidebarGutter() {
  await nextTick();

  const element = sidebar.value;
  if (!element) {
    return;
  }

  if (!window.matchMedia("(min-width: 64rem)").matches) {
    element.style.removeProperty("width");
    return;
  }

  const content = element.querySelector("nav");
  const baseWidth = content?.offsetWidth ?? element.offsetWidth;
  const scrollbarWidth = element.offsetWidth - element.clientWidth;
  const sidebarOverflows = element.scrollHeight > element.clientHeight;
  const needsExternalGutter = scrollbarWidth > 0 && sidebarOverflows;
  const width = `${
    needsExternalGutter ? baseWidth + scrollbarWidth : baseWidth
  }px`;

  if (element.style.width !== width) {
    element.style.width = width;
  }
}

function scheduleSidebarGutterSync() {
  void syncSidebarGutter();

  window.clearTimeout(gutterTimer);
  gutterTimer = window.setTimeout(() => {
    void syncSidebarGutter();
  });
}

watch(() => route.fullPath, scheduleSidebarGutterSync, { flush: "post" });

onMounted(() => {
  window.addEventListener("resize", scheduleSidebarGutterSync);

  nextTick(() => {
    if (menu.value) {
      menu.value.scrollTop = Number(
        sessionStorage.getItem(scrollStorageKey) ?? 0,
      );
    }

    const content = document.querySelector("main");
    if (content) {
      layoutObserver = new ResizeObserver(scheduleSidebarGutterSync);
      layoutObserver.observe(content);
    }

    scheduleSidebarGutterSync();
  });
});

onBeforeUnmount(() => {
  saveScroll();
  window.removeEventListener("resize", scheduleSidebarGutterSync);
  layoutObserver?.disconnect();
  window.clearTimeout(gutterTimer);
});
</script>

<template>
  <aside ref="sidebar" class="drawer-side z-10">
    <label
      for="my-drawer"
      class="drawer-overlay"
      aria-label="关闭侧边栏"
    ></label>
    <nav class="flex min-h-screen w-64 flex-col gap-2 bg-base-200 px-2 pt-10">
      <div class="mx-5 flex items-center gap-2 font-semibold">
        <img src="/favicon.png" alt="" class="size-5 rounded-md" />
        Sub2API 拼车额度
      </div>
      <ul
        ref="menu"
        class="menu w-full overflow-y-auto"
        @scroll.passive="saveScroll"
      >
        <li v-for="group in navigation" :key="group.label">
          <RouterLink v-if="group.to" :to="group.to">
            <AppIcon :name="group.icon" class="size-4" />
            {{ group.label }}
          </RouterLink>
          <details v-else :open="isGroupActive(group)" name="sidebar-group">
            <summary>
              <AppIcon :name="group.icon" class="size-4" />
              {{ group.label }}
              <span v-if="group.label === 'Messages'" class="badge badge-xs"
                >12</span
              >
            </summary>
            <ul>
              <li v-for="item in group.children" :key="item.to">
                <RouterLink :to="item.to">
                  {{ item.label }}
                </RouterLink>
              </li>
            </ul>
          </details>
        </li>
      </ul>
      <SidebarAccountMenu />
    </nav>
  </aside>
</template>
