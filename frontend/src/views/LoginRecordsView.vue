<script setup lang="ts">
import { onMounted, ref } from "vue";

import PageShellHeader from "@/components/common/PageShellHeader.vue";
import { ApiError, api } from "@/services/api";
import type { LoginEventData } from "@/types";

const data = ref<LoginEventData | null>(null);
const loading = ref(true);
const message = ref("");

function dateTime(value: string) {
  return new Date(value).toLocaleString("zh-CN");
}

async function load() {
  loading.value = true;
  message.value = "";
  try {
    data.value = await api<LoginEventData>("login-events?limit=200");
  } catch (error) {
    message.value =
      error instanceof ApiError ? error.message : "加载登录记录失败";
  } finally {
    loading.value = false;
  }
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
      <div class="stat-figure">
        <AppIcon name="shield-check" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">成功登录</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ data?.success_count ?? 0 }}
      </div>
      <div class="stat-desc">包含当前管理员登录</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="shield-exclamation" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">失败尝试</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ data?.failure_count ?? 0 }}
      </div>
      <div class="stat-desc">密码错误、无权限或被限流</div>
    </div>
    <div class="stat">
      <div class="stat-figure">
        <AppIcon name="globe-alt" class="size-7 opacity-40" />
      </div>
      <div class="stat-title">服务端来源 IP</div>
      <div class="stat-value text-xl font-semibold tabular-nums">
        {{ data?.unique_request_ips ?? 0 }}
      </div>
      <div class="stat-desc">按可信代理配置解析</div>
    </div>
  </section>

  <div class="col-span-12 alert alert-info">
    <AppIcon name="information-circle" class="size-5" />
    <span>
      服务端来源 IP 是主要审计依据。WebRTC IP
      由浏览器自报，可能被浏览器隐私策略隐藏，也可能被客户端伪造，只能作为辅助线索。
    </span>
  </div>

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
              <td class="font-mono text-xs">{{ item.request_ip || "—" }}</td>
              <td>
                <div v-if="item.webrtc_ips.length" class="flex flex-wrap gap-1">
                  <span
                    v-for="address in item.webrtc_ips"
                    :key="address"
                    class="badge badge-ghost font-mono badge-sm"
                  >
                    {{ address }}
                  </span>
                </div>
                <span v-else class="text-sm opacity-50">
                  {{ item.webrtc_supported ? "未暴露" : "不支持或已禁用" }}
                </span>
              </td>
              <td class="font-mono text-xs">{{ item.remote_ip || "—" }}</td>
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
    </div>
  </section>
</template>
