<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  page: number;
  totalPages: number;
  total: number;
}>();

const emit = defineEmits<{
  change: [page: number];
}>();

const pages = computed(() => {
  const start = Math.max(1, Math.min(props.page - 2, props.totalPages - 4));
  const end = Math.min(props.totalPages, start + 4);
  return Array.from(
    { length: Math.max(0, end - start + 1) },
    (_, index) => index + start,
  );
});

function change(page: number) {
  if (page >= 1 && page <= props.totalPages && page !== props.page) {
    emit("change", page);
  }
}
</script>

<template>
  <div
    v-if="totalPages > 1"
    class="mt-4 flex flex-wrap items-center justify-between gap-3"
  >
    <span class="text-sm opacity-60">共 {{ total }} 条</span>
    <div class="join">
      <button
        type="button"
        class="btn join-item btn-sm"
        :disabled="page <= 1"
        @click="change(page - 1)"
      >
        上一页
      </button>
      <button
        v-for="item in pages"
        :key="item"
        type="button"
        class="btn join-item btn-sm"
        :class="{ 'btn-active': item === page }"
        :aria-current="item === page ? 'page' : undefined"
        @click="change(item)"
      >
        {{ item }}
      </button>
      <button
        type="button"
        class="btn join-item btn-sm"
        :disabled="page >= totalPages"
        @click="change(page + 1)"
      >
        下一页
      </button>
    </div>
  </div>
</template>
