<script setup lang="ts">
import { computed } from "vue";

import { heroicons } from "./heroicons";

const props = defineProps<{
  name: string;
}>();

const icon = computed(() => {
  const component = heroicons[props.name];

  if (!component) {
    throw new Error(`Unknown Heroicon: ${props.name}`);
  }

  return component;
});
</script>

<template>
  <component :is="icon" aria-hidden="true" />
</template>
