<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useRoute } from "vue-router";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import ConstantAverageAlgorithmTutorial from "./tutorial/components/ConstantAverageAlgorithmTutorial.vue";
import ParticleFilterAlgorithmTutorial from "./tutorial/components/ParticleFilterAlgorithmTutorial.vue";
import TutorialCodeBlock from "./tutorial/components/TutorialCodeBlock.vue";

import { tutorialPages, type TutorialNoteTone } from "./tutorial/tutorialPages";

const route = useRoute();
const article = ref<HTMLElement | null>(null);

const requestedPageId = computed(() =>
  typeof route.query.page === "string" ? route.query.page : "overview",
);
const activePageIndex = computed(() => {
  const index = tutorialPages.findIndex(
    (page) => page.id === requestedPageId.value,
  );
  return index >= 0 ? index : 0;
});
const activePage = computed(() => tutorialPages[activePageIndex.value]);
const activePageId = computed(() => activePage.value.id);
const previousPage = computed(() =>
  activePageIndex.value > 0
    ? tutorialPages[activePageIndex.value - 1]
    : undefined,
);
const nextPage = computed(() =>
  activePageIndex.value < tutorialPages.length - 1
    ? tutorialPages[activePageIndex.value + 1]
    : undefined,
);

const noteClasses: Record<TutorialNoteTone, string> = {
  info: "alert-info",
  warning: "alert-warning",
  success: "alert-success",
};

function tutorialLocation(pageId: string) {
  return pageId === "overview"
    ? "/tutorial"
    : `/tutorial?page=${encodeURIComponent(pageId)}`;
}

watch(activePageId, async () => {
  await nextTick();
  article.value?.scrollIntoView({ block: "start" });
});
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">帮助</RouterLink></li>
          <li><h1>使用教程</h1></li>
        </ul>
      </div>
    </div>
    <RouterLink to="/settings" class="btn btn-primary btn-sm">
      <AppIcon name="cog-6-tooth" class="size-4" />开始配置
    </RouterLink>
  </PageShellHeader>

  <section class="col-span-12 min-w-0">
    <article ref="article" class="card scroll-mt-4 bg-base-200 shadow-xs">
      <div class="card-body gap-0 p-5 sm:p-7 lg:p-9">
        <header class="border-b border-base-300 pb-7">
          <div class="mb-4 badge badge-outline badge-sm">
            {{ activePage.group }}
          </div>
          <div class="flex items-start gap-4">
            <span
              class="flex size-11 shrink-0 items-center justify-center rounded-box bg-base-100"
            >
              <AppIcon :name="activePage.icon" class="size-6" />
            </span>
            <div class="min-w-0">
              <h2 class="text-2xl font-bold sm:text-3xl">
                {{ activePage.title }}
              </h2>
              <p class="mt-3 max-w-3xl text-sm leading-6 opacity-70">
                {{ activePage.summary }}
              </p>
            </div>
          </div>
        </header>

        <ParticleFilterAlgorithmTutorial
          v-if="activePage.interactive === 'particle-filter'"
        />
        <ConstantAverageAlgorithmTutorial
          v-else-if="activePage.interactive === 'constant-average'"
        />
        <div v-else class="divide-y divide-base-300">
          <section
            v-for="section in activePage.sections"
            :key="section.title"
            class="flex flex-col items-stretch py-7 first:pt-7"
          >
            <h3 class="text-lg font-semibold [overflow-wrap:anywhere]">
              {{ section.title }}
            </h3>
            <div
              v-if="section.paragraphs"
              class="mt-4 max-w-4xl space-y-3 text-sm leading-7 opacity-75"
            >
              <p v-for="paragraph in section.paragraphs" :key="paragraph">
                {{ paragraph }}
              </p>
            </div>

            <ol v-if="section.steps" class="mt-5 space-y-4">
              <li
                v-for="(step, index) in section.steps"
                :key="step"
                class="flex items-start gap-3"
              >
                <span class="mt-0.5 badge shrink-0 badge-sm badge-neutral">
                  {{ index + 1 }}
                </span>
                <p class="max-w-4xl text-sm leading-6 opacity-75">
                  {{ step }}
                </p>
              </li>
            </ol>

            <ul
              v-if="section.bullets"
              class="mt-4 max-w-4xl list-disc space-y-2 pl-5 text-sm leading-6 opacity-75"
            >
              <li v-for="item in section.bullets" :key="item">
                {{ item }}
              </li>
            </ul>

            <div
              v-if="section.codeBlocks"
              class="mt-5 flex w-full flex-col gap-4"
              :class="{ 'max-w-4xl': activePage.id !== 'readonly-api' }"
            >
              <TutorialCodeBlock
                v-for="block in section.codeBlocks"
                :key="`${block.title ?? ''}:${block.code}`"
                :block="block"
              />
            </div>
            <div
              v-for="note in section.notes"
              :key="note.title"
              class="mt-5 alert items-start"
              :class="noteClasses[note.tone]"
            >
              <AppIcon
                :name="
                  note.tone === 'warning'
                    ? 'exclamation-triangle'
                    : note.tone === 'success'
                      ? 'check-circle'
                      : 'information-circle'
                "
                class="mt-0.5 size-5 shrink-0"
              />
              <div>
                <h4 class="font-semibold">{{ note.title }}</h4>
                <p class="mt-1 text-sm leading-6">{{ note.text }}</p>
              </div>
            </div>
          </section>
        </div>

        <div
          v-if="activePage.action"
          class="flex border-t border-base-300 py-6"
        >
          <RouterLink :to="activePage.action.to" class="btn btn-primary btn-sm">
            {{ activePage.action.label }}
          </RouterLink>
        </div>

        <nav
          aria-label="教程翻页"
          class="grid gap-3 border-t border-base-300 pt-6 sm:grid-cols-2"
        >
          <RouterLink
            v-if="previousPage"
            v-slot="{ href, navigate }"
            custom
            :to="tutorialLocation(previousPage.id)"
          >
            <a
              :href="href"
              class="rounded-box border border-base-300 bg-base-100 p-4 hover:border-primary"
              @click="navigate"
            >
              <span class="text-xs opacity-50">上一页</span>
              <span class="mt-1 flex items-center gap-2 font-semibold">
                <AppIcon name="arrow-uturn-left" class="size-4" />
                {{ previousPage.title }}
              </span>
            </a>
          </RouterLink>

          <RouterLink
            v-if="nextPage"
            v-slot="{ href, navigate }"
            custom
            :to="tutorialLocation(nextPage.id)"
          >
            <a
              :href="href"
              class="rounded-box border border-base-300 bg-base-100 p-4 text-right hover:border-primary sm:col-start-2"
              @click="navigate"
            >
              <span class="text-xs opacity-50">下一页</span>
              <span
                class="mt-1 flex items-center justify-end gap-2 font-semibold"
              >
                {{ nextPage.title }}
                <AppIcon name="arrow-trending-up" class="size-4" />
              </span>
            </a>
          </RouterLink>
        </nav>
      </div>
    </article>
  </section>
</template>
