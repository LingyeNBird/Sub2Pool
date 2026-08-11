<script setup lang="ts">
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import json from "highlight.js/lib/languages/json";
import plaintext from "highlight.js/lib/languages/plaintext";
import { computed, onBeforeUnmount, ref } from "vue";

import type { TutorialCodeBlock as TutorialCodeBlockData } from "../tutorialPages";

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("json", json);
hljs.registerLanguage("plaintext", plaintext);

const props = defineProps<{
  block: TutorialCodeBlockData;
}>();

const copied = ref(false);
let copiedTimer: ReturnType<typeof window.setTimeout> | undefined;

const language = computed(() => {
  const requested = props.block.language?.toLowerCase() ?? "plaintext";
  const aliases: Record<string, string> = {
    shell: "bash",
    sh: "bash",
    text: "plaintext",
    txt: "plaintext",
  };
  const resolved = aliases[requested] ?? requested;
  return hljs.getLanguage(resolved) ? resolved : "plaintext";
});

const highlightedCode = computed(
  () =>
    hljs.highlight(props.block.code, {
      language: language.value,
      ignoreIllegals: true,
    }).value,
);

function copyWithSelectionFallback() {
  const textarea = document.createElement("textarea");
  textarea.value = props.block.code;
  textarea.readOnly = true;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand("copy");
  } finally {
    textarea.remove();
  }
}

async function copyCode() {
  let succeeded = false;
  try {
    await navigator.clipboard.writeText(props.block.code);
    succeeded = true;
  } catch {
    succeeded = copyWithSelectionFallback();
  }
  if (!succeeded) return;

  copied.value = true;
  if (copiedTimer !== undefined) window.clearTimeout(copiedTimer);
  copiedTimer = window.setTimeout(() => {
    copied.value = false;
  }, 1800);
}

onBeforeUnmount(() => {
  if (copiedTimer !== undefined) window.clearTimeout(copiedTimer);
});
</script>

<template>
  <figure
    class="flex w-full min-w-0 flex-col items-stretch overflow-hidden rounded-box border border-base-300 bg-base-100"
  >
    <figcaption
      class="flex min-h-11 items-center justify-between gap-3 border-b border-base-300 px-3 py-2 sm:px-4"
    >
      <div class="flex min-w-0 items-center gap-2">
        <span v-if="block.title" class="truncate text-xs font-semibold">
          {{ block.title }}
        </span>
        <span class="badge shrink-0 badge-ghost badge-xs">
          {{ language }}
        </span>
      </div>
      <button
        type="button"
        class="btn shrink-0 btn-ghost btn-xs"
        :class="{ 'text-success': copied }"
        :aria-label="copied ? '代码已复制' : '复制代码'"
        @click="copyCode"
      >
        <AppIcon
          :name="copied ? 'check-circle' : 'clipboard-document-check'"
          class="size-4"
        />
        {{ copied ? "已复制" : "复制" }}
      </button>
    </figcaption>
    <pre
      class="w-full min-w-0 overflow-x-auto p-4 text-xs leading-6 sm:p-5"
      :data-language="language"
    ><code class="tutorial-code hljs" v-html="highlightedCode"></code></pre>
  </figure>
</template>

<style scoped>
.tutorial-code :deep(.hljs-attr),
.tutorial-code :deep(.hljs-keyword),
.tutorial-code :deep(.hljs-selector-tag) {
  color: var(--color-primary);
}

.tutorial-code :deep(.hljs-string),
.tutorial-code :deep(.hljs-template-variable) {
  color: var(--color-success);
}

.tutorial-code :deep(.hljs-number),
.tutorial-code :deep(.hljs-literal),
.tutorial-code :deep(.hljs-variable) {
  color: var(--color-secondary);
}

.tutorial-code :deep(.hljs-comment),
.tutorial-code :deep(.hljs-meta) {
  color: color-mix(in oklab, currentColor 45%, transparent);
  font-style: italic;
}
</style>
