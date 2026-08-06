<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import PaginationControls from "@/components/common/PaginationControls.vue";
import { useDateTime } from "@/composables/useDateTime";
import { ApiError, api, jsonBody } from "@/services/api";
import type {
  BlockedIPAddress,
  BlockedIPSource,
  LoginEventData,
  PaginationMeta,
} from "@/types";

interface PendingBlockAction {
  mode: "block" | "unblock";
  address: string;
  sourceType: BlockedIPSource;
  sourceLabel: string;
  eventId: number | null;
  blockId: number | null;
}

const dateTime = useDateTime();
const data = ref<LoginEventData | null>(null);
const blockedAddresses = ref<BlockedIPAddress[]>([]);
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const notes = ref("");
const pendingAction = ref<PendingBlockAction | null>(null);
const blockDialog = ref<HTMLDialogElement | null>(null);
const pagination = ref<PaginationMeta>({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
});

function existingBlock(address: string, sourceType: BlockedIPSource) {
  return blockedAddresses.value.find(
    (item) => item.address === address && item.source_type === sourceType,
  );
}

function openBlock(
  address: string | null,
  sourceType: BlockedIPSource,
  sourceLabel: string,
  eventId: number,
) {
  if (!address || existingBlock(address, sourceType)) return;
  notes.value = "";
  pendingAction.value = {
    mode: "block",
    address,
    sourceType,
    sourceLabel,
    eventId,
    blockId: null,
  };
  blockDialog.value?.showModal();
}

function openUnblock(item: BlockedIPAddress) {
  pendingAction.value = {
    mode: "unblock",
    address: item.address,
    sourceType: item.source_type,
    sourceLabel: item.source_label,
    eventId: item.login_event_id,
    blockId: item.id,
  };
  blockDialog.value?.showModal();
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    const [events, blocks] = await Promise.all([
      api<LoginEventData>(
        `login-events?page=${pagination.value.page}&page_size=${pagination.value.page_size}`,
      ),
      api<BlockedIPAddress[]>("ip-blocks"),
    ]);
    data.value = events;
    pagination.value = events.pagination;
    blockedAddresses.value = blocks;
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载登录记录失败";
  } finally {
    loading.value = false;
  }
}

async function confirmBlockAction() {
  if (!pendingAction.value) return;
  saving.value = true;
  message.value = "";
  try {
    if (pendingAction.value.mode === "block") {
      await api<BlockedIPAddress>("ip-blocks", {
        method: "POST",
        body: jsonBody({
          address: pendingAction.value.address,
          source_type: pendingAction.value.sourceType,
          notes: notes.value,
          login_event_id: pendingAction.value.eventId,
        }),
      });
    } else if (pendingAction.value.blockId != null) {
      await api(`ip-blocks/${pendingAction.value.blockId}`, {
        method: "DELETE",
      });
    }
    blockDialog.value?.close();
    pendingAction.value = null;
    await load();
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "更新封禁列表失败";
  } finally {
    saving.value = false;
  }
}

function changePage(page: number) {
  pagination.value.page = page;
  void load();
}

onMounted(load);
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">安全审计</RouterLink></li>
          <li><h1>登录记录</h1></li>
        </ul>
      </div>
    </div>
    <button class="btn btn-sm" :disabled="loading" @click="load">
      <AppIcon name="arrow-path" class="size-4" />刷新
    </button>
  </PageShellHeader>

  <div v-if="message" class="col-span-12 alert alert-error">
    <AppIcon name="exclamation-triangle" class="size-5" />
    <span>{{ message }}</span>
  </div>

  <section
    class="stats col-span-12 stats-vertical bg-base-200 shadow-xs xl:stats-horizontal"
  >
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">成功登录</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ data?.success_count ?? 0 }}
          </div>
          <div class="stat-desc">包含管理员和普通用户登录</div>
        </div>
        <AppIcon name="shield-check" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">失败尝试</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ data?.failure_count ?? 0 }}
          </div>
          <div class="stat-desc">密码错误、无权限或被限流</div>
        </div>
        <AppIcon name="shield-exclamation" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
    <div class="stat">
      <div class="flex h-full items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="stat-title">服务端来源 IP</div>
          <div class="stat-value text-xl font-semibold tabular-nums">
            {{ data?.unique_request_ips ?? 0 }}
          </div>
          <div class="stat-desc">按可信代理配置解析</div>
        </div>
        <AppIcon name="globe-alt" class="size-7 shrink-0 opacity-40" />
      </div>
    </div>
  </section>

  <div class="col-span-12 alert alert-info">
    <AppIcon name="information-circle" class="size-5" />
    <span>
      服务器来源 IP 和直连地址从首个请求起由后端拦截，不返回页面或正文。WebRTC
      IP 必须等浏览器运行后上报，命中后登录页立即保持空白，并拒绝后续登录请求。
    </span>
  </div>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <div>
        <h2 class="card-title">
          <AppIcon name="no-symbol" class="size-5" />已封禁列表
        </h2>
        <p class="mt-1 text-sm opacity-60">
          所有可识别的封禁地址都不会获得页面或响应正文；WebRTC
          地址在浏览器完成自报后生效，因此首次登录页面仍可能已经传输。
        </p>
      </div>
      <div v-if="blockedAddresses.length" class="overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>类型</th>
              <th>地址</th>
              <th>备注</th>
              <th>封禁时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in blockedAddresses" :key="item.id">
              <td>
                <span class="badge badge-ghost badge-sm">{{
                  item.source_label
                }}</span>
              </td>
              <td class="font-mono text-xs">{{ item.address }}</td>
              <td>{{ item.notes || "—" }}</td>
              <td class="whitespace-nowrap">
                {{ dateTime(item.created_at) }}
              </td>
              <td class="text-right">
                <button
                  class="btn btn-ghost text-error btn-xs"
                  @click="openUnblock(item)"
                >
                  解除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="py-4 text-center text-sm opacity-60">
        当前没有已封禁地址
      </div>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-4">
      <h2 class="card-title">
        <AppIcon name="finger-print" class="size-5" />最近登录尝试
      </h2>
      <div v-if="loading" class="flex justify-center py-10">
        <span class="loading loading-lg loading-spinner"></span>
      </div>
      <div v-else class="overflow-x-auto">
        <table class="table">
          <thead>
            <tr>
              <th>时间</th>
              <th>结果</th>
              <th>用户名</th>
              <th>服务端来源 IP</th>
              <th>WebRTC IP</th>
              <th>直连地址</th>
              <th>浏览器</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in data?.items" :key="item.id">
              <td class="whitespace-nowrap">{{ dateTime(item.created_at) }}</td>
              <td>
                <span
                  class="badge badge-sm"
                  :class="item.success ? 'badge-success' : 'badge-error'"
                >
                  {{ item.success ? "成功" : "失败" }}
                </span>
                <div v-if="item.failure_reason" class="mt-1 text-xs opacity-60">
                  {{ item.failure_reason }}
                </div>
              </td>
              <td>{{ item.username || "—" }}</td>
              <td>
                <button
                  v-if="item.request_ip"
                  class="btn btn-ghost font-mono btn-xs"
                  :disabled="Boolean(existingBlock(item.request_ip, 'request'))"
                  @click="
                    openBlock(
                      item.request_ip,
                      'request',
                      '服务器来源 IP',
                      item.id,
                    )
                  "
                >
                  {{ item.request_ip }}
                  <span
                    v-if="existingBlock(item.request_ip, 'request')"
                    class="badge badge-xs badge-error"
                    >已封禁</span
                  >
                </button>
                <span v-else>—</span>
              </td>
              <td>
                <div v-if="item.webrtc_ips.length" class="flex flex-wrap gap-1">
                  <button
                    v-for="address in item.webrtc_ips"
                    :key="address"
                    class="btn btn-ghost font-mono btn-xs"
                    :disabled="Boolean(existingBlock(address, 'webrtc'))"
                    @click="openBlock(address, 'webrtc', 'WebRTC IP', item.id)"
                  >
                    {{ address }}
                    <span
                      v-if="existingBlock(address, 'webrtc')"
                      class="badge badge-xs badge-error"
                      >已封禁</span
                    >
                  </button>
                </div>
                <span v-else class="text-sm opacity-50">
                  {{ item.webrtc_supported ? "未暴露" : "不支持或已禁用" }}
                </span>
              </td>
              <td>
                <button
                  v-if="item.remote_ip"
                  class="btn btn-ghost font-mono btn-xs"
                  :disabled="Boolean(existingBlock(item.remote_ip, 'remote'))"
                  @click="
                    openBlock(item.remote_ip, 'remote', '直连地址', item.id)
                  "
                >
                  {{ item.remote_ip }}
                  <span
                    v-if="existingBlock(item.remote_ip, 'remote')"
                    class="badge badge-xs badge-error"
                    >已封禁</span
                  >
                </button>
                <span v-else>—</span>
              </td>
              <td>
                <div
                  class="tooltip tooltip-left"
                  :data-tip="item.user_agent || '—'"
                >
                  <div class="max-w-72 truncate text-xs">
                    {{ item.user_agent || "—" }}
                  </div>
                </div>
              </td>
            </tr>
            <tr v-if="!data?.items.length">
              <td colspan="7" class="py-8 text-center opacity-60">
                尚无登录记录
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <PaginationControls
        v-if="!loading"
        :page="pagination.page"
        :total-pages="pagination.total_pages"
        :total="pagination.total"
        @change="changePage"
      />
    </div>
  </section>
  <dialog ref="blockDialog" class="modal">
    <div class="modal-box">
      <form method="dialog">
        <button
          class="btn absolute top-3 right-3 btn-circle btn-ghost btn-sm"
          aria-label="关闭"
        >
          ✕
        </button>
      </form>
      <template v-if="pendingAction">
        <h3 class="text-lg font-bold">
          {{ pendingAction.mode === "block" ? "确认封禁地址" : "确认解除封禁" }}
        </h3>
        <div class="mt-4 rounded-box bg-base-200 p-4">
          <div class="text-sm opacity-60">{{ pendingAction.sourceLabel }}</div>
          <div class="mt-1 font-mono">{{ pendingAction.address }}</div>
        </div>
        <p class="mt-4 text-sm opacity-70">
          <template v-if="pendingAction.mode === 'block'">
            {{
              pendingAction.sourceType === "webrtc"
                ? "浏览器上报该地址后，页面将立即保持空白，后续登录请求不返回正文。首次页面请求发生在上报前，服务端无法提前识别这个地址。"
                : "命中该服务端可见地址时，所有路由都不会返回页面或响应正文。"
            }}
          </template>
          <template v-else>解除后，该地址可再次访问对应路径。</template>
        </p>
        <fieldset v-if="pendingAction.mode === 'block'" class="mt-3 fieldset">
          <label class="label" for="block-notes">备注</label>
          <input
            id="block-notes"
            v-model="notes"
            class="input w-full"
            maxlength="255"
            placeholder="例如：连续登录失败"
          />
        </fieldset>
      </template>
      <div class="modal-action">
        <form method="dialog">
          <button class="btn" :disabled="saving">取消</button>
        </form>
        <button
          class="btn"
          :class="pendingAction?.mode === 'block' ? 'btn-error' : 'btn-primary'"
          :disabled="saving"
          @click="confirmBlockAction"
        >
          <span v-if="saving" class="loading loading-xs loading-spinner"></span>
          {{ pendingAction?.mode === "block" ? "确认封禁" : "确认解除" }}
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button>关闭</button>
    </form>
  </dialog>
</template>
