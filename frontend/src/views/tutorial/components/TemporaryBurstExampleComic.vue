<script setup lang="ts">
import AppIcon from "@/components/common/AppIcon.vue";
import TemporaryBurstActor from "./TemporaryBurstActor.vue";

defineProps<{
  rows: {
    name: string;
    share: number;
    used: number;
    change: number;
    next: number;
  }[];
  note: string;
  carryoverEnabled: boolean;
}>();
const tones = ["a", "b", "c"] as const;
</script>

<template>
  <div
    class="example-comic"
    aria-label="本期消耗与下期权益漫画"
    aria-live="polite"
  >
    <div class="example-speech">{{ note }}</div>
    <div class="example-panels">
      <section class="example-panel" aria-label="本期实际使用">
        <h4 class="example-caption">
          <AppIcon name="banknotes" />这一轮，大家用了多少？
        </h4>
        <div class="example-actors">
          <div
            v-for="(row, index) in rows"
            :key="row.name"
            class="example-person"
          >
            <TemporaryBurstActor
              :name="row.name"
              :tone="tones[index]!"
              :class="{
                'percentage-positive': row.used < row.share,
                'percentage-negative': row.used > row.share,
              }"
              caption="本期使用"
              :value="`${row.used}%`"
              compact
            />
            <span class="contract-ticket">合同 {{ row.share }}%</span>
          </div>
        </div>
        <p class="account-total">
          <AppIcon name="circle-stack" /><span
            >账号共用
            <strong
              >{{ rows.reduce((sum, row) => sum + row.used, 0) }}%</strong
            ></span
          ><span
            >剩余
            <strong
              >{{ 100 - rows.reduce((sum, row) => sum + row.used, 0) }}%</strong
            ></span
          >
        </p>
      </section>
      <div class="example-calendar" aria-hidden="true">
        <AppIcon name="calendar-days" /><span>换周期</span>
      </div>
      <section class="example-panel" aria-label="下一周期可用权益">
        <h4 class="example-caption">
          <AppIcon name="receipt-refund" />下一轮，权益这样分
        </h4>
        <div class="example-actors">
          <div
            v-for="(row, index) in rows"
            :key="row.name"
            class="example-person"
          >
            <span class="change-ticket"
              >{{
                row.change === 0
                  ? "不扣不补"
                  : `${row.change > 0 ? "补" : "还"} ${Math.abs(row.change).toFixed(2)}`
              }}<small v-if="row.change !== 0">个百分点</small></span
            >
            <TemporaryBurstActor
              :name="row.name"
              :tone="tones[index]!"
              :class="{
                'percentage-positive': row.next > row.share,
                'percentage-negative': row.next < row.share,
              }"
              caption="下期权益"
              :value="`${row.next.toFixed(2)}%`"
              compact
            />
          </div>
        </div>
        <p class="account-total">
          <AppIcon name="scale" />{{
            carryoverEnabled
              ? "合同不变，只调整借用的权益。"
              : "本轮不结转，下期按原合同。"
          }}
        </p>
      </section>
    </div>
  </div>
</template>

<style scoped src="./TemporaryBurstExampleComic.css"></style>
