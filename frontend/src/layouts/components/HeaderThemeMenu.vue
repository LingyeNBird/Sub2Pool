<script setup lang="ts">
import { ref } from "vue";

import { themeOptions, type ThemeName, useThemeStore } from "@/stores/theme";

const theme = useThemeStore();
const previewedTheme = ref<ThemeName | null>(null);

function selectTheme(selectedTheme: ThemeName) {
  theme.setTheme(selectedTheme);
  previewedTheme.value = null;
  document.getElementById("header-theme-dropdown")?.hidePopover();
}

function previewThemeOption(themeName: ThemeName) {
  previewedTheme.value = themeName;
  theme.previewTheme(themeName);
}

function restoreSelectedTheme() {
  previewedTheme.value = null;
  theme.restoreTheme();
}

function handlePopoverToggle(event: Event) {
  const popover = event.currentTarget as HTMLElement;

  if (!popover.matches(":popover-open")) {
    restoreSelectedTheme();
  }
}
</script>

<template>
  <div class="z-10">
    <button
      type="button"
      class="btn btn-circle btn-ghost btn-sm"
      aria-label="Choose theme"
      title="Choose theme"
      popovertarget="header-theme-dropdown"
      style="anchor-name: --header-theme-anchor"
    >
      <AppIcon name="swatch" class="size-4" />
    </button>
    <div
      id="header-theme-dropdown"
      class="dropdown dropdown-end mt-3 max-h-[min(32rem,calc(100vh-5rem))] w-[min(32rem,calc(100vw-2rem))] overflow-y-auto rounded-box border border-base-300 bg-base-100 p-3 shadow-2xl [&:not(:popover-open)]:pointer-events-none"
      popover
      style="position-anchor: --header-theme-anchor"
      @toggle="handlePopoverToggle"
      @mouseleave="restoreSelectedTheme"
    >
      <ul
        class="menu grid w-full grid-cols-2 gap-2 p-0"
        aria-label="Theme selection"
      >
        <li v-for="option in themeOptions" :key="option.name">
          <button
            type="button"
            class="relative flex h-10 items-stretch overflow-hidden rounded-box border border-base-300 bg-base-100 p-0 text-base-content"
            :class="{
              'outline-2 outline-primary outline-solid':
                theme.current === option.name,
              'outline-2 outline-secondary outline-solid':
                previewedTheme === option.name && theme.current !== option.name,
            }"
            :data-theme="option.name"
            :aria-pressed="theme.current === option.name"
            @click="selectTheme(option.name)"
            @mouseenter="previewThemeOption(option.name)"
          >
            <span
              class="flex min-w-0 grow items-center px-2 text-[0.7rem] leading-5 sm:px-3 sm:text-sm"
            >
              <span class="truncate text-left">{{ option.label }}</span>
            </span>
            <span
              class="flex w-[36%] shrink-0 self-stretch border-s border-base-content/20 sm:w-24"
              aria-hidden="true"
            >
              <span class="relative flex-[2] bg-primary">
                <AppIcon
                  v-if="theme.current === option.name"
                  name="check"
                  class="absolute inset-0 m-auto size-3 text-primary-content"
                />
              </span>
              <span
                class="flex-1 border-s border-base-content/20 bg-secondary"
              ></span>
              <span
                class="flex-1 border-s border-base-content/20 bg-accent"
              ></span>
              <span
                class="flex-1 border-s border-base-content/20 bg-neutral"
              ></span>
            </span>
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>
