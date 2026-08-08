<script setup lang="ts">
defineProps<{
  label: string;
  help?: string;
}>();
</script>

<template>
  <div class="label justify-start gap-1">
    <span>{{ label }}</span>
    <span
      v-if="help"
      class="responsive-help-tooltip tooltip tooltip-bottom z-10"
      :data-tip="help"
    >
      <button
        type="button"
        class="btn btn-circle cursor-help btn-ghost btn-xs"
        :aria-label="`查看${label}说明`"
      >
        ?
      </button>
    </span>
  </div>
</template>
