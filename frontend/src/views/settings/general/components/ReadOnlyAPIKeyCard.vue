<script setup lang="ts">
import { nextTick, ref } from "vue";

import { useDateTime } from "@/composables/useDateTime";

const { configured, hint, createdAt, generating, revoking } = defineProps<{
  configured: boolean;
  hint: string;
  createdAt: string | null;
  generating: boolean;
  revoking: boolean;
}>();

const emit = defineEmits<{
  generate: [];
  revoke: [];
}>();

const formatDateTime = useDateTime();
const keyDialog = ref<HTMLDialogElement | null>(null);
const plaintextKey = ref("");
const copied = ref(false);

async function reveal(apiKey: string) {
  plaintextKey.value = apiKey;
  copied.value = false;
  await nextTick();
  keyDialog.value?.showModal();
}

async function copyKey() {
  if (!plaintextKey.value) return;
  await navigator.clipboard.writeText(plaintextKey.value);
  copied.value = true;
}

function closeKeyDialog() {
  keyDialog.value?.close();
}

function clearPlaintext() {
  plaintextKey.value = "";
  copied.value = false;
}

defineExpose({ reveal });
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-5">
      <div>
        <h2 class="card-title">
          <AppIcon name="key" class="size-5" />只读 API
        </h2>
      </div>

      <div class="alert items-start alert-info">
        <AppIcon name="information-circle" class="mt-0.5 size-5 shrink-0" />
        <div class="min-w-0 text-sm leading-6">
          <p>
            这枚 Key
            可读取全部已开放的额度业务数据，包括监控账号、额度总览、参与者、观测与
            FAST 明细、粒子轨迹、统计和通知正文。
          </p>
          <p class="mt-1">
            它不继承普通系统用户的页面或参与者范围。只应交给受信任的服务端程序，并通过
            HTTPS 传输。
          </p>
          <RouterLink
            to="/tutorial?page=readonly-api"
            class="mt-2 inline-flex link font-medium link-primary"
          >
            查看端点、参数与响应文档
          </RouterLink>
        </div>
      </div>

      <div v-if="configured" class="rounded-box bg-base-100 p-4">
        <div class="text-xs opacity-60">当前 API Key</div>
        <div class="mt-1 font-mono text-lg font-semibold tracking-wide">
          ************{{ hint }}
        </div>
        <div class="mt-2 text-xs opacity-50">
          生成时间：{{ formatDateTime(createdAt) }}
        </div>
      </div>
      <div v-else class="rounded-box bg-base-100 p-4 text-sm opacity-70">
        尚未生成 API Key，外部只读接口当前不可访问。
      </div>

      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="generating || revoking"
          @click="emit('generate')"
        >
          <span
            v-if="generating"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="key" class="size-4" />
          {{ configured ? "重新生成" : "生成 API Key" }}
        </button>
        <button
          v-if="configured"
          type="button"
          class="btn btn-outline btn-error btn-sm"
          :disabled="generating || revoking"
          @click="emit('revoke')"
        >
          <span
            v-if="revoking"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="trash" class="size-4" />
          废弃 API Key
        </button>
      </div>
    </div>
  </section>

  <Teleport to="body">
    <dialog ref="keyDialog" class="modal" @close="clearPlaintext">
      <div class="modal-box max-w-2xl">
        <div class="flex items-start gap-3">
          <AppIcon name="key" class="mt-0.5 size-6 shrink-0 text-primary" />
          <div class="min-w-0 grow">
            <h2 class="text-lg font-bold">保存只读 API Key</h2>
            <p class="mt-2 text-sm leading-6 opacity-70">
              完整 Key
              只在本次显示。关闭后系统只保留摘要和尾号，无法再次查看或找回。
            </p>
          </div>
        </div>
        <textarea
          class="textarea mt-5 h-28 w-full resize-none font-mono text-sm"
          readonly
          :value="plaintextKey"
          aria-label="只读 API Key"
          @focus="($event.target as HTMLTextAreaElement).select()"
        ></textarea>
        <div class="modal-action">
          <button type="button" class="btn" @click="closeKeyDialog">
            关闭
          </button>
          <button type="button" class="btn btn-primary" @click="copyKey">
            <AppIcon
              :name="copied ? 'check-circle' : 'clipboard-document-check'"
              class="size-4"
            />
            {{ copied ? "已复制" : "复制 API Key" }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button type="button" @click="closeKeyDialog">关闭</button>
      </form>
    </dialog>
  </Teleport>
</template>
