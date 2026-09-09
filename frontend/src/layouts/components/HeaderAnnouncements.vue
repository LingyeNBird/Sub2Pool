<script setup lang="ts">
import { onMounted, ref } from "vue";

import { ApiError, api, jsonBody } from "@/services/api";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import PricingGroupDialog from "@/components/common/PricingGroupDialog.vue";
import type { AppSettingsData, UpstreamPricingState } from "@/types/settings";
import type { ConfirmDialogHandle } from "@/types/common";
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
const confirmation = ref<ConfirmDialogHandle | null>(null);
const reverting = ref(false);
const applying = ref(false);
const enabling = ref(false);
const notice = ref("");
const pricingDialog = ref<InstanceType<typeof PricingGroupDialog> | null>(null);

function applyPricing(item: AnnouncementRecord) {
  if (!item.can_apply_pricing || applying.value || reverting.value) return;
  pricingDialog.value?.open([], async (groupIds) => {
    applying.value = true;
    notice.value = "";
    let failure = "";
    let consumed = false;
    try {
      await api<UpstreamPricingState>("settings/upstream-pricing/apply", {
        method: "POST",
        body: jsonBody({
          confirm: true,
          announcement: true,
          group_ids: groupIds,
        }),
      });
      consumed = true;
    } catch (cause) {
      failure = cause instanceof ApiError ? cause.message : "上游计费应用失败";
      if (cause instanceof ApiError) {
        const details = cause.details as
          | { upstream_pricing?: UpstreamPricingState }
          | undefined;
        consumed = Boolean(details?.upstream_pricing?.announcement_applied_at);
      }
    } finally {
      await loadAnnouncements();
      consumed ||=
        announcements.value.find((record) => record.code === item.code)
          ?.can_apply_pricing === false;
      if (consumed) {
        const current = announcements.value.find(
          (record) => record.code === item.code,
        );
        if (current) current.can_apply_pricing = false;
        if (failure)
          error.value = `${failure}；公告应用入口已使用，请在设置页核对分组结果并重试。`;
        else notice.value = "推荐计费修正已应用并读回确认。";
      }
      window.dispatchEvent(new Event("sub2pool:upstream-pricing-changed"));
      applying.value = false;
    }
    return consumed ? null : failure || "未能确认应用结果，请刷新后核对。";
  });
}

async function enableRecommendations() {
  if (enabling.value) return;
  enabling.value = true;
  notice.value = "";
  let failure = "";
  try {
    await api<AppSettingsData>("settings", {
      method: "PATCH",
      body: jsonBody({ auto_apply_recommendations: true }),
    });
    window.dispatchEvent(
      new Event("sub2pool:auto-apply-recommendations-enabled"),
    );
    notice.value =
      "已开启自动应用建议额度，将按监控流程自动应用符合条件的额度建议。";
  } catch (cause) {
    failure =
      cause instanceof ApiError ? cause.message : "开启自动应用建议额度失败";
  } finally {
    await loadAnnouncements();
    if (failure) error.value = failure;
    enabling.value = false;
  }
}

async function revertPricing(item: AnnouncementRecord) {
  if (
    !item.can_revert ||
    reverting.value ||
    !(await confirmation.value?.open({
      title: "撤回上游计费配置？",
      message:
        "将恢复接管前的 Sub2API 计费字段，不再自动应用，也不会重新启用本地修正。",
      confirmLabel: "确认撤回",
      tone: "warning",
    }))
  )
    return;
  reverting.value = true;
  let failure = "";
  try {
    await api("settings/upstream-pricing/revert", {
      method: "POST",
      body: jsonBody({ confirm: true, revision: item.pricing_revision }),
    });
  } catch (cause) {
    failure = cause instanceof ApiError ? cause.message : "上游计费撤回失败";
  } finally {
    await loadAnnouncements();
    if (failure) error.value = failure;
    window.dispatchEvent(new Event("sub2pool:upstream-pricing-changed"));
    reverting.value = false;
  }
}

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
  notice.value = "";
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
            <p v-if="notice" role="status" class="alert text-sm alert-success">
              {{ notice }}
            </p>
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
                <div class="flex flex-wrap gap-2">
                  <button
                    v-if="item.can_apply_pricing"
                    type="button"
                    class="btn btn-primary btn-sm"
                    :disabled="applying || reverting"
                    @click="applyPricing(item)"
                  >
                    {{ applying ? "应用中…" : "一键应用修正" }}
                  </button>
                  <button
                    v-if="item.pricing_status"
                    type="button"
                    class="btn btn-outline btn-sm"
                    :disabled="!item.can_revert || reverting || applying"
                    @click="revertPricing(item)"
                  >
                    {{ reverting ? "撤回中…" : "撤回上游计费配置" }}
                  </button>
                  <button
                    v-if="item.auto_apply_recommendations !== undefined"
                    type="button"
                    class="btn btn-primary btn-sm"
                    :disabled="item.auto_apply_recommendations || enabling"
                    @click="enableRecommendations"
                  >
                    {{
                      enabling
                        ? "开启中…"
                        : item.auto_apply_recommendations
                          ? "已开启"
                          : "一键开启"
                    }}
                  </button>
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
    <ConfirmDialog ref="confirmation" />
    <PricingGroupDialog
      ref="pricingDialog"
      description="将为勾选分组设置 FAST 2.5 倍、gpt-6* 模型 1.8 倍，并关闭长上下文阶梯计费，影响组内所有用户。确认请求被接受后，公告的一键应用入口永久消失；失败可在设置页重试，撤回入口保留。"
    />
  </div>
</template>
