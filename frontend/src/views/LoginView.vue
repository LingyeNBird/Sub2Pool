<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

import { ApiError } from "@/services/api";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();
const username = ref("");
const password = ref("");
const loading = ref(false);
const message = ref("");

async function submit() {
  loading.value = true;
  message.value = "";
  try {
    await auth.signIn(username.value, password.value);
    await router.replace("/");
  } catch (error) {
    message.value = error instanceof ApiError ? error.message : "登录失败";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="hero min-h-screen bg-base-200">
    <div class="hero-content w-full max-w-md">
      <section class="card w-full bg-base-100 shadow-xs">
        <div class="card-body gap-5">
          <div>
            <div class="mb-3 flex items-center gap-2 font-semibold">
              <AppIcon name="scale" class="size-5" />
              Sub2API 拼车额度
            </div>
            <h1 class="card-title text-2xl">管理员登录</h1>
            <p class="mt-2 text-sm opacity-60">
              登录后查看测算结果和手动额度建议。
            </p>
          </div>
          <div v-if="message" class="alert alert-soft text-sm alert-error">
            <AppIcon name="exclamation-triangle" class="size-5" />
            <span>{{ message }}</span>
          </div>
          <form class="grid gap-4" @submit.prevent="submit">
            <fieldset class="fieldset">
              <label class="label" for="username">用户名</label>
              <input
                id="username"
                v-model="username"
                class="input w-full"
                autocomplete="username"
                required
                autofocus
              />
            </fieldset>
            <fieldset class="fieldset">
              <label class="label" for="password">密码</label>
              <input
                id="password"
                v-model="password"
                type="password"
                class="input w-full"
                autocomplete="current-password"
                required
              />
            </fieldset>
            <button class="btn w-full btn-primary" :disabled="loading">
              <span
                v-if="loading"
                class="loading loading-sm loading-spinner"
              ></span>
              {{ loading ? "正在登录" : "登录" }}
            </button>
          </form>
        </div>
      </section>
    </div>
  </main>
</template>
