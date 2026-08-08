<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute } from "vue-router";

import AppHeader from "./components/AppHeader.vue";
import AppSidebar from "./components/AppSidebar.vue";

const route = useRoute();
const drawerOpen = ref(false);

watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false;
  },
);
</script>

<template>
  <div class="drawer min-h-screen bg-base-200 lg:drawer-open">
    <input
      id="my-drawer"
      v-model="drawerOpen"
      type="checkbox"
      class="drawer-toggle"
      aria-label="Toggle sidebar"
    />
    <main
      class="drawer-content min-w-0 bg-base-100 lg:mt-2 lg:rounded-ss-4xl lg:border-s lg:border-t lg:border-base-300 lg:bg-[radial-gradient(ellipse_50rem_30rem_at_50%_0%,color-mix(oklch(96%_0.008_68)_3%,transparent)_0%,transparent_80%)] lg:[corner-start-start-shape:squircle]"
    >
      <div class="grid min-w-0 grid-cols-12 gap-6 p-4 lg:p-10">
        <AppHeader />
        <RouterView />
      </div>
    </main>
    <AppSidebar />
  </div>
</template>
