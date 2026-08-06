<script setup lang="ts">
withDefaults(
  defineProps<{
    compact?: boolean;
  }>(),
  {
    compact: false,
  },
);
</script>

<template>
  <header
    class="col-span-12 flex flex-col lg:flex-row lg:items-end"
    :class="compact ? 'gap-3' : 'gap-4'"
  >
    <slot />
  </header>
</template>
