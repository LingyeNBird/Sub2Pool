<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ApiError, api, jsonBody } from "@/services/api";
import type { Participant } from "@/types/participants";
import type { CPABinding, CPAClaim, CPAKeys } from "@/types/cpa";
import { useDateTime, useZonedDateTimeIso } from "@/composables/useDateTime";
import { useAuthStore } from "@/stores/auth";
import { formatCurrency } from "@/utils/formatters";

const formatDateTime = useDateTime();
const toIso = useZonedDateTimeIso();
const auth = useAuthStore();
const exactEnd = ref("");
const initialEnd = ref("");
const props = defineProps<{ participants: Participant[] }>();
const data = ref<CPAKeys>({ keys: [], unregistered: [] });
const participantId = ref<number | null>(null);
const observedHash = ref("");
const rawKey = ref("");
const name = ref("");
const busy = ref(false);
const message = ref("");
const claim = ref<CPAClaim | null>(null);
const claimKeyId = ref<number | null>(null);
const claimParticipantId = ref<number | null>(null);
const startedAt = ref("");
const endedAt = ref("");
const dialog = ref<HTMLDialogElement | null>(null);
const available = computed(() => [
  ...data.value.keys
    .filter((key) => !key.bindings.some((binding) => !binding.ended_at))
    .map((key) => ({
      observed_hash: key.observed_hash,
      hint: key.hint,
      name: key.name,
    })),
  ...data.value.unregistered.map((key) => ({ ...key, name: "" })),
]);
const bindings = computed(() =>
  data.value.keys
    .flatMap((key) => key.bindings)
    .sort((a, b) => b.started_at.localeCompare(a.started_at)),
);

function failure(reason: unknown) {
  message.value = reason instanceof ApiError ? reason.message : "操作失败";
}
async function load() {
  data.value = await api<CPAKeys>("cpa/keys");
}
async function bind() {
  if (!participantId.value || busy.value) return;
  busy.value = true;
  message.value = "";
  try {
    const result = await api<CPABinding>("cpa/keys", {
      method: "POST",
      body: jsonBody({
        participant_id: participantId.value,
        name: name.value,
        ...(observedHash.value
          ? { observed_hash: observedHash.value }
          : { raw_key: rawKey.value }),
      }),
    });
    rawKey.value = "";
    observedHash.value = "";
    name.value = "";
    await load();
    message.value =
      "Key 已绑定，从生效时间开始归属。需要认领更早的请求时，请打开历史认领。";
    claimKeyId.value = result.key_id;
    claimParticipantId.value = result.participant_id;
  } catch (reason) {
    failure(reason);
  } finally {
    busy.value = false;
  }
}
async function unbind(binding: CPABinding) {
  busy.value = true;
  message.value = "";
  try {
    await api(`cpa/bindings/${binding.id}`, { method: "DELETE" });
    await load();
    message.value = "已结束绑定，旧请求归属保留。";
  } catch (reason) {
    failure(reason);
  } finally {
    busy.value = false;
  }
}
function openClaim(binding: CPABinding) {
  claim.value = null;
  message.value = "";
  claimKeyId.value = binding.key_id;
  claimParticipantId.value = binding.participant_id;
  startedAt.value = "";
  // A preceding claim ends exactly where this binding starts.
  exactEnd.value = binding.started_at;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: auth.timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(binding.started_at))
      .map((part) => [part.type, part.value]),
  );
  initialEnd.value = `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
  endedAt.value = initialEnd.value;
  dialog.value?.showModal();
}
async function rename(binding: CPABinding, event: Event) {
  busy.value = true;
  message.value = "";
  try {
    await api(`cpa/bindings/${binding.id}`, {
      method: "PATCH",
      body: jsonBody({ name: (event.target as HTMLInputElement).value }),
    });
    await load();
  } catch (reason) {
    failure(reason);
  } finally {
    busy.value = false;
  }
}
async function preview() {
  busy.value = true;
  message.value = "";
  claim.value = null;
  try {
    claim.value = await api<CPAClaim>("cpa/claims/preview", {
      method: "POST",
      body: jsonBody({
        key_id: claimKeyId.value,
        participant_id: claimParticipantId.value,
        started_at: toIso(startedAt.value),
        ended_at:
          endedAt.value === initialEnd.value
            ? exactEnd.value
            : toIso(endedAt.value),
      }),
    });
  } catch (reason) {
    failure(reason);
  } finally {
    busy.value = false;
  }
}
async function apply() {
  if (!claim.value) return;
  busy.value = true;
  message.value = "";
  try {
    await api(`cpa/claims/${claim.value.id}/apply`, { method: "POST" });
    claim.value = null;
    await load();
    dialog.value?.close();
    message.value = "历史请求已认领，受影响账号的额度已重新计算。";
  } catch (reason) {
    claim.value = null;
    failure(reason);
  } finally {
    busy.value = false;
  }
}
watch([startedAt, endedAt, claimParticipantId], () => {
  claim.value = null;
});
onMounted(() => {
  void load().catch(failure);
});
</script>

<template>
  <section
    class="card col-span-12 min-w-0 bg-base-200 shadow-xs"
    data-testid="cpa-key-manager"
  >
    <div class="card-body gap-4">
      <h2 class="card-title">CPA Key 与成员绑定</h2>
      <p class="text-sm opacity-60">
        一个成员可绑定多个 Key。仅保存不可逆摘要和末四位；成员使用系统账号登录。
      </p>
      <form
        class="grid gap-3 md:grid-cols-2 xl:grid-cols-4"
        @submit.prevent="bind"
      >
        <label class="grid gap-1 text-sm"
          >参与者<select
            aria-label="参与者"
            v-model="participantId"
            class="select w-full"
            required
          >
            <option :value="null" disabled>选择参与者</option>
            <option
              v-for="participant in props.participants.filter((p) => p.enabled)"
              :key="participant.id"
              :value="participant.id"
            >
              {{ participant.name }}
            </option>
          </select></label
        >
        <label class="grid gap-1 text-sm"
          >已采集 Key<select
            aria-label="已采集 Key"
            v-model="observedHash"
            class="select w-full"
            @change="rawKey = ''"
          >
            <option value="">输入完整 Key 预先绑定</option>
            <option
              v-for="key in available"
              :key="key.observed_hash"
              :value="key.observed_hash"
            >
              {{ key.name || "API Key" }} ····{{ key.hint }} ·
              {{ key.observed_hash.slice(0, 8) }}
            </option>
          </select></label
        >
        <label v-if="!observedHash" class="grid gap-1 text-sm"
          >CPA Key<input
            v-model="rawKey"
            type="password"
            autocomplete="off"
            class="input w-full"
            :required="!observedHash"
            maxlength="4096"
        /></label>
        <label class="grid gap-1 text-sm"
          >Key 备注<input
            v-model="name"
            class="input w-full"
            maxlength="80"
            placeholder="如：个人电脑"
        /></label>
        <button class="btn justify-self-start" :disabled="busy">
          绑定 Key
        </button>
      </form>
      <p v-if="message" role="status" class="alert">{{ message }}</p>
      <div class="overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>Key</th>
              <th>参与者</th>
              <th>生效时间</th>
              <th>结束时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="binding in bindings" :key="binding.id">
              <td>
                <input
                  :value="binding.name"
                  class="input max-w-44 input-sm"
                  maxlength="80"
                  :disabled="busy"
                  :aria-label="`Key ${binding.hint} 备注`"
                  placeholder="API Key 备注"
                  @change="rename(binding, $event)"
                />
                ····{{ binding.hint }}
              </td>
              <td>{{ binding.participant_name }}</td>
              <td>{{ formatDateTime(binding.started_at) }}</td>
              <td>
                {{
                  binding.ended_at
                    ? formatDateTime(binding.ended_at)
                    : "持续生效"
                }}
              </td>
              <td>
                <div class="flex gap-2">
                  <button
                    class="btn btn-sm"
                    :disabled="busy"
                    @click="openClaim(binding)"
                  >
                    历史认领</button
                  ><button
                    v-if="!binding.ended_at"
                    class="btn btn-sm"
                    :disabled="busy"
                    @click="unbind(binding)"
                  >
                    解绑
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="!bindings.length">
              <td colspan="5">
                暂无绑定。先添加参与者，再选择或输入 CPA Key。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
  <dialog id="cpa-claim-dialog" ref="dialog" class="modal">
    <div class="modal-box max-w-3xl">
      <h2 class="text-lg font-semibold">认领已有 CPA 请求</h2>
      <p class="mt-2 text-sm opacity-60">
        选择未归属的历史时间范围（{{
          auth.timezone
        }}）。结束时间不包含在认领范围内，默认衔接当前绑定的生效时间。
      </p>
      <form class="mt-4 grid gap-3" @submit.prevent="preview">
        <label class="grid gap-1 text-sm"
          >认领给<select
            v-model="claimParticipantId"
            class="select w-full"
            required
          >
            <option
              v-for="participant in participants.filter((p) => p.enabled)"
              :key="participant.id"
              :value="participant.id"
            >
              {{ participant.name }}
            </option>
          </select></label
        >
        <label class="grid gap-1 text-sm"
          >开始时间<input
            v-model="startedAt"
            type="datetime-local"
            class="input w-full"
            required
        /></label>
        <label class="grid gap-1 text-sm"
          >结束时间<input
            v-model="endedAt"
            type="datetime-local"
            step="1"
            class="input w-full"
            required
        /></label>
        <button class="btn justify-self-start" :disabled="busy">
          预览历史用量
        </button>
      </form>
      <p v-if="message" role="status" class="mt-4 alert">{{ message }}</p>
      <div v-if="claim" class="mt-4 grid gap-3">
        <p class="text-sm">{{ claim.historical_contract_policy }}</p>
        <div
          v-for="account in claim.accounts"
          :key="account.account_id"
          class="card bg-base-200"
        >
          <div class="card-body gap-1 p-4">
            <h3 class="font-semibold">{{ account.account_name }}</h3>
            <p>
              {{ account.request_count }} 次请求 ·
              {{ account.token_count.toLocaleString() }} Token ·
              {{ formatCurrency(account.usage_usd) }}
            </p>
            <p v-if="account.unpriced_request_count">
              {{ account.unpriced_request_count }} 条未计价
            </p>
            <p v-if="!account.coverage.complete">
              此范围采集不完整，仅认领已经收到的请求。
            </p>
          </div>
        </div>
        <p class="text-xs opacity-60">
          预览有效至
          {{ formatDateTime(claim.expires_at) }}，数据变化后需重新预览。
        </p>
        <button class="btn" :disabled="busy" @click="apply">
          确认认领这些请求
        </button>
      </div>
      <form method="dialog" class="modal-action">
        <button class="btn" :disabled="busy">关闭</button>
      </form>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button :disabled="busy">关闭</button>
    </form>
  </dialog>
</template>
