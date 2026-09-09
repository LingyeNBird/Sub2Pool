<script setup lang="ts">
import AppIcon from "@/components/common/AppIcon.vue";
import TemporaryBurstActor from "./TemporaryBurstActor.vue";
</script>

<template>
  <div class="notice-comic" aria-label="重置卡改变车友时间安排的小漫画">
    <section class="notice-panel">
      <span class="notice-date"
        ><AppIcon name="calendar-days" />周一 · 原计划</span
      >
      <div class="notice-dialogue">前两天先忙工作，<br />后五天我再来用！</div>
      <TemporaryBurstActor name="A" tone="a" compact />
      <p>下一次重置：下周一</p>
    </section>
    <section class="notice-panel">
      <span class="notice-date"
        ><AppIcon name="bolt" />周三 · 没商量就用卡</span
      >
      <div class="notice-dialogue">两天蹬光了？<br />管理员，来张重置卡！</div>
      <div class="notice-duo">
        <TemporaryBurstActor name="B" tone="b" compact /><TemporaryBurstActor
          name="管理员"
          tone="admin"
          compact
        />
      </div>
      <p><AppIcon name="ticket" />手动用卡，重新开始一周</p>
    </section>
    <section class="notice-panel notice-punchline">
      <span class="notice-date"><AppIcon name="clock" />A 回来一看</span>
      <div class="notice-dialogue">
        我还没上车，<br />我的下周一先被蹬走了？！
      </div>
      <TemporaryBurstActor name="A" tone="a" compact />
      <p>下次重置：周一 → <strong>周三</strong></p>
    </section>
  </div>
</template>

<style scoped src="./TemporaryBurstNoticeComic.css"></style>
