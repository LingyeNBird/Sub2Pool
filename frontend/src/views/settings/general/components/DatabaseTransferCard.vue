<script setup lang="ts">
import { ref } from "vue";

defineProps<{ exporting: boolean; importing: boolean }>();
const emit = defineEmits<{
  export: [];
  import: [event: Event];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
</script>

<template>
  <section
    class="card mb-6 inline-block w-full break-inside-avoid bg-base-200 shadow-xs"
  >
    <div class="card-body">
      <h2 class="card-title">
        <AppIcon name="circle-stack" class="size-5" />数据库迁移
      </h2>
      <p class="text-sm leading-6 opacity-70">
        导出文件包含参与者、账本、统计、通知、登录记录、管理员账号以及全部系统设置。
        导入会完整覆盖当前数据库，并在服务器数据目录保留
        pinche.before-import.sqlite3 作为覆盖前副本。
      </p>
      <div class="alert text-sm alert-warning">
        <AppIcon name="exclamation-triangle" class="size-5" />
        <span>
          加密后的 Admin Token、SMTP 密码和 Resend Key 依赖部署环境中的
          DJANGO_SECRET_KEY。迁移服务器时还必须安全复制
          .env，数据库备份不会包含环境变量。
        </span>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="btn btn-sm"
          :disabled="exporting || importing"
          @click="emit('export')"
        >
          <span
            v-if="exporting"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="arrow-down-tray" class="size-4" />
          导出完整数据库
        </button>
        <button
          class="btn btn-outline btn-error btn-sm"
          :disabled="importing || exporting"
          @click="fileInput?.click()"
        >
          <span
            v-if="importing"
            class="loading loading-xs loading-spinner"
          ></span>
          <AppIcon v-else name="arrow-up-tray" class="size-4" />
          导入并覆盖
        </button>
        <input
          ref="fileInput"
          type="file"
          accept=".sqlite3,.sqlite,.db,application/vnd.sqlite3"
          class="hidden"
          @change="emit('import', $event)"
        />
      </div>
    </div>
  </section>
</template>
