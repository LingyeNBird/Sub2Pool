<script setup lang="ts">
import type { ParticipantViewMode } from "../types";

const viewMode = defineModel<ParticipantViewMode>({ required: true });
</script>

<template>
  <div class="join">
    <button
      type="button"
      class="btn join-item btn-sm"
      :class="{ 'btn-active': viewMode === 'cards' }"
      @click="viewMode = 'cards'"
    >
      <AppIcon name="squares-2x2" class="size-4" />
      卡片
    </button>
    <button
      type="button"
      class="btn join-item btn-sm"
      :class="{ 'btn-active': viewMode === 'table' }"
      @click="viewMode = 'table'"
    >
      <AppIcon name="table-cells" class="size-4" />
      表格
    </button>
  </div>
</template>
