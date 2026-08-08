<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api, jsonBody } from "@/services/api";
import type {
  BlockedIPAddress,
  BlockedIPSource,
  LoginEventData,
  PaginationMeta,
} from "@/types";

import BlockedAddressTable from "./components/BlockedAddressTable.vue";
import IPBlockDialog from "./components/IPBlockDialog.vue";
import LoginAttemptTable from "./components/LoginAttemptTable.vue";
import LoginStats from "./components/LoginStats.vue";
import type { IPBlockDialogHandle, PendingBlockAction } from "./types";

const data = ref<LoginEventData | null>(null);
const blockedAddresses = ref<BlockedIPAddress[]>([]);
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const blockDialog = ref<IPBlockDialogHandle | null>(null);
const pagination = ref<PaginationMeta>({
  page: 1,
  page_size: 20,
  total: 0,
  total_pages: 1,
});

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

function openBlock(
  address: string,
  sourceType: BlockedIPSource,
  sourceLabel: string,
  eventId: number,
) {
  blockDialog.value?.openBlock(address, sourceType, sourceLabel, eventId);
}

async function confirmBlockAction(action: PendingBlockAction, notes: string) {
  saving.value = true;
  message.value = "";
  try {
    if (action.mode === "block") {
      await api<BlockedIPAddress>("ip-blocks", {
        method: "POST",
        body: jsonBody({
          address: action.address,
          source_type: action.sourceType,
          notes,
          login_event_id: action.eventId,
        }),
      });
    } else if (action.blockId != null) {
      await api(`ip-blocks/${action.blockId}`, { method: "DELETE" });
    }
    blockDialog.value?.close();
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

  <LoginStats
    :success-count="data?.success_count ?? 0"
    :failure-count="data?.failure_count ?? 0"
    :unique-request-ips="data?.unique_request_ips ?? 0"
  />
  <div class="col-span-12 alert alert-info">
    <AppIcon name="information-circle" class="size-5" />
    <span>
      服务器来源 IP 和直连地址从首个请求起由后端拦截，不返回页面或正文。WebRTC
      IP 必须等浏览器运行后上报，命中后登录页立即保持空白，并拒绝后续登录请求。
    </span>
  </div>
  <BlockedAddressTable
    :blocked-addresses="blockedAddresses"
    @unblock="blockDialog?.openUnblock($event)"
  />
  <LoginAttemptTable
    :rows="data?.items ?? []"
    :blocked-addresses="blockedAddresses"
    :pagination="pagination"
    :loading="loading"
    @block="openBlock"
    @page="changePage"
  />
  <IPBlockDialog
    ref="blockDialog"
    :saving="saving"
    @confirm="confirmBlockAction"
  />
</template>
