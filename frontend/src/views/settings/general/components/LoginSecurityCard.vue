<script setup lang="ts">
import type { PasswordForm } from "../composables/useSettingsPage";

const form = defineModel<PasswordForm>("form", { required: true });
const emit = defineEmits<{ change: [] }>();
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="lock-closed" class="size-5" />登录安全
      </h2>
      <fieldset class="fieldset">
        <label class="label">当前密码</label>
        <input
          v-model="form.old_password"
          type="password"
          class="input w-full"
          autocomplete="current-password"
        />
      </fieldset>
      <div class="grid gap-3 md:grid-cols-2">
        <fieldset class="fieldset">
          <label class="label">新密码</label>
          <input
            v-model="form.new_password"
            type="password"
            class="input w-full"
            autocomplete="new-password"
          />
        </fieldset>
        <fieldset class="fieldset">
          <label class="label">确认新密码</label>
          <input
            v-model="form.confirm_password"
            type="password"
            class="input w-full"
            autocomplete="new-password"
          />
        </fieldset>
      </div>
      <button class="btn btn-sm" @click="emit('change')">修改密码</button>
      <p class="text-sm opacity-60">
        站点密钥、Cookie Secure 和允许域名属于部署安全边界，仅通过 Docker
        环境变量配置。
      </p>
    </div>
  </section>
</template>
