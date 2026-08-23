<script setup lang="ts">
import { onMounted, ref } from "vue";

import { api } from "@/services/api";
import type {
  AnnouncementListData,
  AnnouncementRecord,
} from "@/types/security";

const dialog = ref<HTMLDialogElement | null>(null);
const announcements = ref<AnnouncementRecord[]>([]);
const unreadCount = ref(0);
const loading = ref(false);
const error = ref("");
const markingCode = ref("");

async function loadAnnouncements() {
  loading.value = true;
  error.value = "";
  try {
    const data = await api<AnnouncementListData>("announcements");
    announcements.value = data.items;
    unreadCount.value = data.unread_count;
  } catch {
    error.value = "公告读取失败，请稍后重试。";
  } finally {
    loading.value = false;
  }
}

function open() {
  dialog.value?.showModal();
  void loadAnnouncements();
}

async function markRead(item: AnnouncementRecord) {
  if (item.read || markingCode.value) return;
  markingCode.value = item.code;
  error.value = "";
  try {
    const updated = await api<AnnouncementRecord>(
      `announcements/${encodeURIComponent(item.code)}/read`,
      { method: "POST" },
    );
    Object.assign(item, updated);
    unreadCount.value = announcements.value.filter(
      (announcement) => !announcement.read,
    ).length;
  } catch {
    error.value = "已读状态保存失败，请稍后重试。";
  } finally {
    markingCode.value = "";
  }
}

function publishedDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

onMounted(() => {
  void loadAnnouncements();
});
</script>

<template>
  <div class="relative">
    <button
      type="button"
      class="btn btn-circle btn-ghost btn-sm"
      aria-label="查看公告"
      title="公告"
      aria-haspopup="dialog"
      @click="open"
    >
      <AppIcon name="bell" class="size-4" />
      <span
        v-if="unreadCount"
        class="absolute -top-1 -right-1 badge h-4 min-w-4 border-0 bg-error px-1 text-[10px] text-error-content"
      >
        {{ unreadCount > 99 ? "99+" : unreadCount }}
      </span>
    </button>

    <Teleport to="body">
      <dialog ref="dialog" class="modal">
        <div
          class="modal-box flex max-h-[calc(100dvh-2rem)] max-w-3xl flex-col overflow-hidden p-0"
        >
          <header
            class="flex shrink-0 items-start justify-between gap-4 border-b border-base-300 px-5 py-4 sm:px-6"
          >
            <h2 class="text-lg font-bold">系统公告</h2>
            <form method="dialog">
              <button
                class="btn btn-circle btn-ghost btn-sm"
                aria-label="关闭公告"
              >
                <AppIcon name="x-mark" class="size-4" />
              </button>
            </form>
          </header>

          <div
            class="min-h-0 flex-1 space-y-3 overflow-y-auto bg-base-200/40 p-4 sm:p-6"
          >
            <div v-if="loading" class="flex justify-center py-12">
              <span class="loading loading-spinner"></span>
            </div>
            <div
              v-else-if="error && !announcements.length"
              class="alert alert-error"
            >
              <AppIcon name="exclamation-triangle" class="size-5 shrink-0" />
              <span>{{ error }}</span>
              <button
                class="btn btn-sm"
                type="button"
                @click="loadAnnouncements"
              >
                重试
              </button>
            </div>
            <div
              v-else-if="!announcements.length"
              class="py-12 text-center text-sm opacity-60"
            >
              暂无公告
            </div>
            <article
              v-for="item in announcements"
              v-else
              :key="item.code"
              class="card border bg-base-100 shadow-xs"
              :class="
                item.read
                  ? 'border-base-300'
                  : item.severity === 'warning'
                    ? 'border-warning/50'
                    : 'border-info/50'
              "
            >
              <div class="card-body gap-3 p-4 sm:p-5">
                <div class="flex flex-wrap items-start justify-between gap-3">
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2">
                      <h3 class="font-semibold">{{ item.title }}</h3>
                      <span
                        v-if="!item.read"
                        class="badge badge-sm badge-error"
                      >
                        未读
                      </span>
                    </div>
                    <time class="mt-1 block text-xs opacity-50">
                      {{ publishedDate(item.published_at) }}
                    </time>
                  </div>
                  <button
                    type="button"
                    class="btn btn-sm"
                    :class="item.read ? 'btn-ghost' : 'btn-primary'"
                    :disabled="item.read || Boolean(markingCode)"
                    @click="markRead(item)"
                  >
                    <span
                      v-if="markingCode === item.code"
                      class="loading loading-xs loading-spinner"
                    ></span>
                    <AppIcon
                      v-else-if="item.read"
                      name="check"
                      class="size-4"
                    />
                    {{ item.read ? "已读" : "标记已读" }}
                  </button>
                </div>
                <div class="space-y-2 text-sm leading-6 opacity-75">
                  <p v-for="paragraph in item.paragraphs" :key="paragraph">
                    {{ paragraph }}
                  </p>
                </div>
              </div>
            </article>
            <div
              v-if="error && announcements.length"
              class="alert text-sm alert-error"
            >
              <AppIcon name="exclamation-triangle" class="size-4 shrink-0" />
              <span>{{ error }}</span>
            </div>
          </div>
        </div>
        <form method="dialog" class="modal-backdrop">
          <button>关闭公告</button>
        </form>
      </dialog>
    </Teleport>
  </div>
</template>
