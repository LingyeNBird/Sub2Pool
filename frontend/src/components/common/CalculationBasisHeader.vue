<script setup lang="ts">
defineProps<{
  title: string;
  help: string;
}>();
</script>

<template>
  <div class="flex items-center gap-2 pr-10">
    <h3 class="text-lg font-bold">{{ title }}</h3>
    <div
      class="responsive-help-tooltip tooltip tooltip-bottom"
      :data-tip="help"
    >
      <button
        type="button"
        class="btn btn-circle cursor-help btn-ghost btn-xs"
        :aria-label="help"
      >
        ?
      </button>
    </div>
  </div>
</template>
