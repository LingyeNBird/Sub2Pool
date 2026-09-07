<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ApiError, api } from "@/services/api";
import type { ResearchState, ResearchSummary } from "@/types/research";
import { useDateTime } from "@/composables/useDateTime";

const props = defineProps<{ demo?: boolean }>();
const state = ref<ResearchState | null>(null);
const form = reactive({
  enabled: false,
  projects: [] as string[],
  endpoint: "https://codex.nightunderfly.online",
  interval_hours: 6,
  gateway_only: false,
});
const saving = ref(false);
const error = ref("");
const notice = ref("");
const consent = ref<HTMLDialogElement | null>(null);
const withdrawal = ref<HTMLDialogElement | null>(null);
const accepted = ref(false);
const dateTime = useDateTime();
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
let fetching = false;
let readVersion = 0;
const summary = computed(() =>
  state.value?.summary?.requests !== undefined
    ? (state.value.summary as ResearchSummary)
    : null,
);
const statusNames: Record<string, string> = {
  disabled: "未开启",
  no_data: "等待第一份本地记录",
  scheduled: "等待科研进程",
  analyzing: "分析中",
  analyzed: "已完成本地分析",
  sent: "统计已发送",
  unchanged: "统计未变，无需重复发送",
  destination_unconfigured: "仅本地分析 · 接收网站待配置",
  insufficient_data: "样本不足，暂不发送",
  delivery_failed: "发送失败，等待重试",
  analysis_failed: "分析失败，等待重试",
  withdrawing: "撤回中",
  withdrawal_failed: "已停止发送，撤回需重试",
  withdrawn: "统计已撤回",
  unidentifiable: "用量组成难以区分，暂不归因",
  model_mismatch: "候选模型均不适配",
  drift_sensitive: "结论对额度波动假设敏感",
  exploratory: "探索性比较，非计费机制证明",
  external_usage_uncontrolled: "尚未确认账号用量完整性",
};
async function load(syncForm = false) {
  if ((fetching && !syncForm) || props.demo) return;
  const version = ++readVersion;
  fetching = true;
  try {
    const data = await api<ResearchState>("settings/research");
    if (disposed || version !== readVersion) return;
    state.value = data;
    if (syncForm)
      Object.assign(form, {
        enabled: data.enabled,
        projects: [...data.projects],
        endpoint: data.endpoint,
        interval_hours: data.interval_hours,
        gateway_only: data.gateway_only,
      });
  } catch (cause) {
    if (!disposed && version === readVersion)
      error.value =
        cause instanceof ApiError ? cause.message : "科研状态读取失败";
  } finally {
    if (version === readVersion) fetching = false;
  }
}
function requestSave() {
  error.value = notice.value = "";
  if (form.enabled) {
    accepted.value = false;
    consent.value?.showModal();
  } else void save(false);
}
async function save(withConsent: boolean) {
  if (withConsent && !accepted.value) return;
  ++readVersion;
  fetching = false;
  saving.value = true;
  error.value = "";
  try {
    const data = await api<ResearchState>("settings/research", {
      method: "PATCH",
      body: JSON.stringify({
        ...form,
        accept_consent: withConsent,
        policy_version: state.value?.policy_version,
      }),
    });
    state.value = data;
    Object.assign(form, {
      enabled: data.enabled,
      projects: [...data.projects],
      endpoint: data.endpoint,
      interval_hours: data.interval_hours,
      gateway_only: data.gateway_only,
    });
    consent.value?.close();
    notice.value = data.enabled
      ? "科研授权已保存；任务由独立进程定时执行。"
      : "科研共创已关闭。";
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : "科研设置保存失败";
  } finally {
    saving.value = false;
  }
}
async function stop() {
  ++readVersion;
  fetching = false;
  saving.value = true;
  try {
    state.value = await api<ResearchState>("settings/research", {
      method: "PATCH",
      body: JSON.stringify({ enabled: false }),
    });
    form.enabled = false;
    notice.value =
      "已停止后续发送；已经发出的请求可能完成，历史贡献可单独撤回。";
    error.value = "";
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : "停止科研失败，请重试";
  } finally {
    saving.value = false;
  }
}
async function run() {
  saving.value = true;
  try {
    await api("settings/research/run", { method: "POST" });
    notice.value =
      "已安排本地分析；符合授权和发送条件时自动提交汇总，不额外调用模型。";
    await load();
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : "任务安排失败";
  } finally {
    saving.value = false;
  }
}
async function withdraw() {
  ++readVersion;
  fetching = false;
  saving.value = true;
  try {
    await api("settings/research/withdraw", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    });
    notice.value = "已停止分享并撤回本安装在该网站的全部历史贡献。";
    withdrawal.value?.close();
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : "撤回失败；可再次重试";
  } finally {
    saving.value = false;
    await load(true);
  }
}
function preventSavingClose(event: Event) {
  if (saving.value) event.preventDefault();
}
onMounted(async () => {
  await load(true);
  if (!disposed && !props.demo)
    timer = setInterval(() => {
      if (!saving.value) void load();
    }, 15000);
});
onBeforeUnmount(() => {
  disposed = true;
  if (timer) clearInterval(timer);
});
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body gap-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="card-title">
          <AppIcon name="chart-bar" class="size-5" />科研共创
        </h2>
        <span class="badge badge-ghost badge-sm">默认关闭 · 自愿参与</span>
      </div>
      <p class="text-sm opacity-70">
        用本地原始用量与额度快照，向共研中心贡献 GPT-6
        分项证据。不采集请求内容，不改变你的额度测算或计费规则。
      </p>
      <p v-if="demo" class="alert text-sm alert-info">
        演示站不启用科研、不保存授权，也不发送任何数据。
      </p>
      <template v-else-if="state">
        <p class="text-sm" role="status">
          {{ statusNames[state.last_status] ?? state.last_status }}
        </p>
        <div
          v-if="state.last_error || error"
          class="alert text-sm alert-error"
          role="alert"
        >
          {{ error || state.last_error }}
        </div>
        <div v-if="notice" class="alert text-sm alert-success" role="status">
          {{ notice }}
        </div>
        <fieldset class="min-w-0 space-y-3" :disabled="saving">
          <label class="flex items-center gap-3 font-medium"
            ><input
              v-model="form.enabled"
              class="toggle toggle-sm"
              type="checkbox"
            />开启科研共创（保存并确认后生效）</label
          >
          <label
            v-for="project in state.available_projects"
            :key="project.id"
            class="flex items-center gap-3 rounded-box bg-base-100 p-3"
            ><input
              v-model="form.projects"
              :value="project.id"
              type="checkbox"
              class="checkbox checkbox-sm"
            />{{ project.title }}</label
          >
          <label class="flex min-w-0 flex-col gap-1 text-sm"
            ><span>科研接收网站</span
            ><input
              v-model.trim="form.endpoint"
              type="url"
              class="input w-full min-w-0 font-mono text-sm"
              maxlength="512"
              placeholder="https://codex.nightunderfly.online"
          /></label>
          <p class="text-xs break-words opacity-65">
            默认接收网站已配置。也可填写你信任的 HTTPS
            根地址；更换网站须重新授权，不会自动撤回旧贡献。留空时仅本地分析。
          </p>
          <label class="flex flex-wrap items-center gap-2 text-sm"
            ><span>分析间隔（小时）</span
            ><input
              v-model.number="form.interval_hours"
              class="input w-24 input-sm"
              type="number"
              min="1"
              max="168"
          /></label>
          <label class="flex items-start gap-2 text-sm"
            ><input
              v-model="form.gateway_only"
              type="checkbox"
              class="checkbox mt-1 shrink-0 checkbox-sm"
            /><span
              >我确认研究涉及的账号在研究期间只经此 Sub2API 使用，没有 ChatGPT
              网页端、其他网关或未采集调用消耗同一额度池。无法确认也可贡献，会标记覆盖不确定性，而不是拒绝参与。</span
            ></label
          >
          <div class="flex flex-wrap gap-2">
            <button
              type="button"
              class="btn btn-primary btn-sm"
              @click="requestSave"
            >
              保存科研设置</button
            ><button
              v-if="state.enabled"
              type="button"
              class="btn btn-outline btn-sm"
              @click="stop"
            >
              立即停止分享</button
            ><button
              v-if="state.consent_current"
              type="button"
              class="btn btn-sm"
              @click="run"
            >
              现在分析
            </button>
          </div>
        </fieldset>
        <div
          class="rounded-box bg-base-100 p-3 text-xs leading-relaxed opacity-80"
        >
          上次分析：{{ dateTime(state.last_computed_at) }}<br />下次计划：{{
            dateTime(state.next_run_at)
          }}<br />上次确认发送：{{ dateTime(state.last_sent_at) }}
        </div>
        <div v-if="summary" class="space-y-3">
          <h3 class="font-semibold">本安装 · 持久科研批次</h3>
          <p class="text-sm">
            {{ summary.requests.toLocaleString() }} 次请求（GPT-6
            {{ summary.gpt6_requests.toLocaleString() }} 次） · ${{
              summary.raw_usd.toLocaleString()
            }}
            标准成本 · {{ summary.quota_points }} 额度百分点 ·
            {{ summary.batches }} 个批次 / {{ summary.intervals }} 个可对齐区间
          </p>
          <p class="text-xs opacity-65">
            无请求数、周期数、区间数或本地置信度门槛，一条请求也能贡献。中心联合各份证据，不要求你独立研究成功。
            FAST 固定 2 倍，GPT-5.6/6
            长上下文不额外翻倍，其他模型按既有正确计费；只研究 GPT-6 四分项。
            不采集、不上传、不使用粒子滤波或平均恒定估值，也没有估值辅助分析。
          </p>
          <p v-if="summary.archived_batches" class="text-xs text-warning">
            {{ summary.archived_batches }}
            个批次的原始历史有缺口，已保留原有证据，不以残缺数据覆盖或自动删除贡献。
          </p>
          <p v-if="summary.intervals === 0" class="text-xs opacity-65">
            当前记录仍可贡献规模；尚无可对齐的原始额度区间，不伪造原因支持度。
          </p>
          <details class="rounded-box bg-base-100 p-3">
            <summary class="cursor-pointer text-sm font-medium">
              查看待分享的统计内容
            </summary>
            <p class="mt-2 text-xs opacity-65">
              预览一个批次的全部统计字段，此外只附协议/方法版本、随机公钥与批次标识、递增版本和签名。不同批次分别提交，原始请求与时间线留在本地。
            </p>
            <pre class="mt-2 max-h-72 overflow-auto text-xs">{{
              JSON.stringify(summary.preview, null, 2)
            }}</pre>
          </details>
        </div>
        <details class="text-sm">
          <summary class="cursor-pointer font-medium">
            发送内容与隐私边界
          </summary>
          <p v-for="line in state.privacy" :key="line" class="mt-2 opacity-70">
            {{ line }}
          </p>
        </details>
        <button
          v-if="state.can_withdraw"
          class="btn btn-outline text-error btn-sm"
          :disabled="saving"
          @click="withdrawal?.showModal()"
        >
          停止并撤回已提交统计
        </button>
      </template>
      <p v-else-if="error" class="text-sm text-error">
        {{ error }}
        <button type="button" class="link" @click="load(true)">重试</button>
      </p>
      <p v-else class="text-sm opacity-60">正在读取科研设置…</p>
    </div>
  </section>
  <Teleport to="body">
    <dialog
      ref="consent"
      class="modal"
      aria-labelledby="research-consent-title"
      @cancel="preventSavingClose"
    >
      <div class="modal-box max-w-2xl">
        <h2 id="research-consent-title" class="text-lg font-bold">
          确认科研共创授权
        </h2>
        <p class="mt-3 text-sm">
          接收网站：<strong class="break-all">{{
            form.endpoint || "https://codex.nightunderfly.online"
          }}</strong>
        </p>
        <p class="mt-2 text-sm">
          项目：{{ form.projects.length ? "GPT-6 额度异常归因" : "尚未选择" }} ·
          每 {{ form.interval_hours }} 小时分析一次
        </p>
        <p v-for="line in state?.privacy" :key="line" class="mt-3 text-sm">
          {{ line }}
        </p>
        <p class="mt-3 text-sm font-semibold">
          只做本地分析也会在本机保留新的原始成本分项，供后续可重复计算；缺少历史事实不会额外从上游补采。若改动目的地或研究范围，会再次要求确认。
        </p>
        <label class="mt-4 flex items-start gap-2 text-sm"
          ><input
            v-model="accepted"
            type="checkbox"
            class="checkbox shrink-0 checkbox-sm"
            :disabled="saving"
          /><span
            >我已了解具体统计内容、接收网站和网络层 IP
            可见性，同意按所选范围分享去标识化统计。</span
          ></label
        >
        <p v-if="error" class="mt-3 text-sm text-error" role="alert">
          {{ error }}
        </p>
        <div class="modal-action">
          <button
            class="btn btn-sm"
            :disabled="saving"
            @click="consent?.close()"
          >
            取消</button
          ><button
            class="btn btn-primary btn-sm"
            :disabled="!accepted || saving"
            @click="save(true)"
          >
            同意并开启
          </button>
        </div>
      </div>
    </dialog>
    <dialog
      ref="withdrawal"
      class="modal"
      aria-labelledby="research-withdraw-title"
      @cancel="preventSavingClose"
    >
      <div class="modal-box">
        <h2 id="research-withdraw-title" class="text-lg font-bold">
          停止并撤回统计？
        </h2>
        <p class="mt-3 text-sm break-words">
          会向上次发送或尝试发送的网站
          {{ state?.last_sent_endpoint }}
          发送签名撤回请求，并停止后续分享。网站会删除本安装的历史批次及当前统计，保留无统计内容的版本墓碑防止旧请求恢复数据。外部缓存或部署备份不保证立即消失。
        </p>
        <p v-if="error" class="mt-3 text-error" role="alert">{{ error }}</p>
        <div class="modal-action">
          <button
            class="btn btn-sm"
            :disabled="saving"
            @click="withdrawal?.close()"
          >
            取消</button
          ><button
            class="btn btn-error btn-sm"
            :disabled="saving"
            @click="withdraw"
          >
            确认撤回
          </button>
        </div>
      </div>
    </dialog>
  </Teleport>
</template>
