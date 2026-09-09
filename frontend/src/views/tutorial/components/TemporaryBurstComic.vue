<script setup lang="ts">
import AppIcon from "@/components/common/AppIcon.vue";
import TemporaryBurstActor from "./TemporaryBurstActor.vue";

const riders = [
  { name: "A", tone: "a", share: 50 },
  { name: "B", tone: "b", share: 25 },
  { name: "C", tone: "c", share: 25 },
] as const;
const endings = [
  {
    key: "carry",
    title: "选择结转：借用的权益下周还",
    mode: "结转模式",
    total: 100,
    usage: [33, 33, 34],
    next: [67, 17, 16],
    changes: ["+17", "−8", "−9"],
    bubble: "借走的权益，下周归还。",
    explanation: "B 多用 8，C 多用 9，补偿给少用 17 的 A。",
    result: "下周补偿 / 扣除",
    icon: "arrows-right-left",
  },
  {
    key: "no-carry",
    title: "选择不结转：本轮多用不追账",
    mode: "不结转模式",
    total: 100,
    usage: [33, 33, 34],
    next: [50, 25, 25],
    changes: ["不变", "不变", "不变"],
    bubble: "说好了，这轮不追账！",
    explanation: "同样用了 33 / 33 / 34，A 不获补偿，B、C 不扣下期权益。",
    result: "下周仍按原合同",
    icon: "check-circle",
  },
] as const;
</script>

<template>
  <figure class="burst-comic" aria-labelledby="burst-comic-title">
    <figcaption class="comic-heading">
      <AppIcon name="bolt" class="comic-heading-icon" />
      <div>
        <h3 id="burst-comic-title">一轮爽蹬，两个结局</h3>
        <p>从管理员开启，到下一周的权益分配</p>
      </div>
    </figcaption>

    <section
      class="comic-panel opening-scene"
      aria-label="管理员选择模式后开启爽蹬"
    >
      <div class="panel-caption"><span>1</span>先选模式，再开蹬</div>
      <div class="opening-cast">
        <div class="admin-scene">
          <div class="speech speech-admin">
            结转，还是不结转？<br />不结转，先通知大家！
          </div>
          <TemporaryBurstActor name="管理员" tone="admin" />
          <span class="activation-stamp"
            ><AppIcon name="bolt" />开启临时爽蹬</span
          >
        </div>
        <div class="riders-contract">
          <p class="scene-label">
            <AppIcon
              name="chat-bubble-left-right"
            />不结转须通知；结转无需逐一通知
          </p>
          <div class="rider-line">
            <TemporaryBurstActor
              v-for="rider in riders"
              :key="rider.name"
              :name="rider.name"
              :tone="rider.tone"
              caption="原合同"
              :value="`${rider.share}%`"
            />
          </div>
        </div>
      </div>
    </section>

    <div class="middle-scenes">
      <section
        class="comic-panel wallet-scene"
        aria-label="自动或手动应用建议余额"
      >
        <div class="panel-caption"><span>2</span>把余额真正放开</div>
        <div class="wallet-art">
          <AppIcon name="banknotes" class="wallet-banknotes" />
          <div class="wallet-face">
            <span>每位参与者的建议余额</span
            ><strong><small>$</small>9999</strong>
          </div>
          <AppIcon name="credit-card" class="wallet-clasp" />
        </div>
        <div class="application-choices">
          <p>
            <AppIcon name="check-circle" /><span
              >已开自动应用<strong>系统尝试写入余额</strong></span
            >
          </p>
          <p>
            <AppIcon name="cursor-arrow-rays" /><span
              >未开自动应用<strong>管理员手动点「应用」</strong></span
            >
          </p>
        </div>
        <p class="panel-footnote">应用成功才生效；9999 不是新增套餐额度。</p>
      </section>

      <section
        class="comic-panel spending-scene"
        aria-label="车友按需使用并持续记录消费"
      >
        <div class="panel-caption"><span>3</span>大家按需用，系统照常记账</div>
        <div class="spending-art">
          <div class="shared-account">
            <AppIcon name="server" /><span>同一个上游账号</span
            ><AppIcon name="bolt" class="spending-bolt" />
          </div>
          <div class="rider-line spending-riders">
            <div
              v-for="rider in riders"
              :key="rider.name"
              class="spending-rider"
            >
              <TemporaryBurstActor
                :name="rider.name"
                :tone="rider.tone"
                compact
              />
              <span class="usage-token"><AppIcon name="banknotes" />消费</span>
            </div>
          </div>
          <div class="ledger-slip">
            <AppIcon name="document-chart-bar" />
            <div>
              <strong>实际消耗，一笔不少</strong
              ><span>套餐总量不变，合同份额不变</span>
            </div>
          </div>
        </div>
      </section>
    </div>

    <section class="turning-page" aria-label="换周期后按所选模式处理">
      <div class="calendar-art">
        <AppIcon name="calendar-days" /><span>新周期</span>
      </div>
      <div>
        <h4>翻到下一周，按开启时选定的模式处理</h4>
        <p>恢复普通余额建议；是否结转不取决于账号用了多少。</p>
      </div>
      <AppIcon name="arrow-path" class="page-turn-icon" />
    </section>

    <div class="story-fork" aria-hidden="true">
      <span></span><b>同样的消费，两种模式</b><span></span>
    </div>
    <div class="comic-endings">
      <section
        v-for="ending in endings"
        :key="ending.key"
        class="comic-panel ending"
        :class="`ending-${ending.key}`"
        :aria-label="ending.title"
      >
        <div class="ending-heading">
          <span class="condition-tag">{{ ending.mode }}</span>
          <h4>{{ ending.title }}</h4>
        </div>
        <div class="consumption-summary">
          <div class="quota-dial" :style="{ '--used': `${ending.total}%` }">
            <div>
              <AppIcon name="circle-stack" /><strong
                >{{ ending.total }}<small>%</small></strong
              ><span>账号总用量</span>
            </div>
          </div>
          <div class="consumption-people">
            <div v-for="(rider, index) in riders" :key="rider.name">
              <AppIcon name="user" :class="`person-${rider.tone}`" /><b>{{
                rider.name
              }}</b
              ><span
                >用了 <strong>{{ ending.usage[index] }}%</strong></span
              >
            </div>
          </div>
        </div>
        <div class="speech ending-speech">{{ ending.bubble }}</div>
        <p class="ending-explanation">{{ ending.explanation }}</p>
        <div class="next-week-label">
          <AppIcon :name="ending.icon" /><strong>{{ ending.result }}</strong>
        </div>
        <div class="rider-line ending-riders">
          <div
            v-for="(rider, index) in riders"
            :key="rider.name"
            class="next-rider"
          >
            <span
              class="adjustment-ticket"
              :class="{ 'ticket-unchanged': ending.key === 'no-carry' }"
              >{{ ending.changes[index]
              }}<small v-if="ending.key === 'carry'"> 个百分点</small></span
            >
            <TemporaryBurstActor
              :name="rider.name"
              :tone="rider.tone"
              :value="`${ending.next[index]}%`"
              caption="下周权益"
              compact
            />
          </div>
        </div>
        <p v-if="ending.key === 'no-carry'" class="ending-footer">
          <AppIcon name="check-circle" />开启前，所有车友都应知道本轮不结转。
        </p>
        <p v-else class="ending-footer">
          <AppIcon name="scale" />补偿 17，扣除 8 + 9，彼此相抵。
        </p>
      </section>
    </div>

    <div class="comic-last-panel">
      <AppIcon name="clipboard-document-check" />
      <p>
        <strong>最后，把新一周的普通余额建议应用回去。</strong
        ><span>自动应用已开则由系统处理；未开则由管理员手动应用。</span>
      </p>
    </div>
    <p class="comic-boundary">
      图中按 A 50% / B 25% / C 25%
      的合同、无旧结转举例。结转的是借用的权益，不是储存上游流量；手动提前终止后，本轮不再产生后续结转。
    </p>
  </figure>
</template>

<style scoped src="./TemporaryBurstComic.css"></style>
