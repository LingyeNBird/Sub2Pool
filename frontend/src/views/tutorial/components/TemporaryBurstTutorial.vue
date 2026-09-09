<script setup lang="ts">
import { computed, ref } from "vue";
import TemporaryBurstComic from "./TemporaryBurstComic.vue";
import TemporaryBurstExampleComic from "./TemporaryBurstExampleComic.vue";
import TemporaryBurstNoticeComic from "./TemporaryBurstNoticeComic.vue";
const examples = [
  {
    title: "整轮用满",
    usage: [33, 33, 34],
    note: "B 借用 8 个百分点，C 借用 9 个百分点；A 让出了 17 个百分点，全部补偿给 A。",
  },
  {
    title: "B 超用 5 个百分点",
    usage: [20, 30, 5],
    note: "结转模式下，B 借用的 5 个百分点仍需归还：A 获补偿 3，C 获补偿 2，不取决于账号是否用满。",
  },
  {
    title: "有人用满，但没人超用",
    usage: [50, 15, 10],
    note: "A 用满自己的合同，B、C 没用满：没人借用别人的权益，下一周期不新增补偿或扣除。",
  },
  {
    title: "所有人都没用满",
    usage: [30, 20, 20],
    note: "没有超用，也就没有借用。剩余 30 个百分点当期过期，不凭空增加下一周期总权益。",
  },
  {
    title: "A 本轮没有使用",
    usage: [0, 50, 50],
    note: "A 的 50 个百分点被 B、C 各借用 25。结转模式下，下一周期 A 为 100%，B、C 为 0%；不是把所有闲置额度无条件存起来。",
  },
];
const selected = ref(0);
const carryoverEnabled = ref(true);
const example = computed(() => examples[selected.value]!);
const rows = computed(() => {
  const base = [50, 25, 25];
  const unused = base.map((share, index) =>
    Math.max(0, share - example.value.usage[index]!),
  );
  const over = base.map((share, index) =>
    Math.max(0, example.value.usage[index]! - share),
  );
  const spare = unused.reduce((sum, value) => sum + value, 0);
  const excess = over.reduce((sum, value) => sum + value, 0);
  const borrowed = carryoverEnabled.value ? Math.min(spare, excess) : 0;
  return base.map((share, index) => {
    const change =
      (spare ? (borrowed * unused[index]!) / spare : 0) -
      (excess ? (borrowed * over[index]!) / excess : 0);
    return {
      name: ["A", "B", "C"][index]!,
      share,
      used: example.value.usage[index]!,
      change,
      next: share + change,
    };
  });
});
</script>

<template>
  <div class="space-y-8 py-7">
    <TemporaryBurstComic />
    <section>
      <h3 class="text-lg font-semibold">从首页开启</h3>
      <ol class="mt-3 list-inside list-decimal space-y-3 text-sm leading-7">
        <li>
          进入“额度总览”，在“当前额度建议”下方找到“临时爽蹬”。确保所有启用的
          Sub2API 账号都有当前周期的有效观测和完整参与者归属。
        </li>
        <li>
          先选择“结转”或“不结转”，再点击“开启临时爽蹬”。只有不结转模式需要告知所有车友并勾选确认；结转模式无需逐一通知。仅管理员可以开启，本轮不能中途切换模式。
        </li>
        <li>
          已开启自动应用建议时，立即尝试设置所有参与者余额为
          9999；未开启时，余额不会直接变化，需要从额度建议手动应用。
        </li>
        <li>
          新周期不再建议
          9999，恢复普通建议；结转模式计入借用的补偿或扣除，不结转模式不产生新调整。未开启自动应用时，需要手动应用普通建议，把上游高余额收回来。
        </li>
      </ol>
      <p class="mt-4 alert text-sm alert-warning">
        9999 是余额，不是 9999%
        权益。它可能让一个人迅速耗尽全组订阅额度；上游自身的限速、余额权限和套餐限制仍然有效。
      </p>
      <div
        class="mt-4 rounded-box border border-warning/40 bg-warning/10 p-5 text-sm leading-7"
      >
        <h4 class="font-semibold">为什么不结转爽蹬必须提前告知所有车友？</h4>
        <TemporaryBurstNoticeComic />
        <p>
          下面画的是“不结转、却没通知”的情况：A
          计划晚几天使用，其他车友提前耗尽并使用重置卡，A
          不仅时间安排被打乱，少用的权益也不会获得下期补偿。
        </p>
        <p>
          因此，不结转模式要告知所有车友。结转模式无需逐一通知，被借用的权益会在后续周期补偿，也能帮助暂时用不上的车友安排后续使用；但未被借用的闲置额度不会保留。
        </p>
        <p class="mt-3 font-medium">
          两种模式都不会自动使用重置卡或充值。提前用卡仍建议沟通时间安排；权益补偿不是上游容量储存，也不保证任何时刻都有可用额度。
        </p>
      </div>
    </section>
    <section>
      <h3 class="text-lg font-semibold">提前终止爽蹬</h3>
      <p class="mt-3 text-sm leading-7">
        点击首页红色“提前终止爽蹬”，在“你确定提前终止爽蹬吗？”弹窗确认后，立即恢复普通额度建议，停止本轮加速采样与提醒，取消本轮全部后续结转。已超用不追账，少用不补偿；即使原来选了结转模式也一样。
      </p>
      <p class="mt-3 text-sm leading-7">
        自动应用已开时系统尝试写回普通余额，失败项需重试；未开时须手动应用，才能收回上游高余额。提前终止不可撤销，本周期不能再次开启。
      </p>
    </section>
    <section>
      <h3 class="text-lg font-semibold">本轮用满提醒</h3>
      <p class="mt-3 text-sm leading-7">
        先在系统设置中配置邮件服务和管理员接收邮箱，建议发送测试邮件确认可达。开启爽蹬后，可在卡片切换“用满提醒”；默认关闭，只对本轮有效。
        本轮原周期账号的最新有效观测达到 95%
        时开始提醒，以后仍达阈值则每半小时最多发一封该账号的邮件。95%
        表示接近用满，并不等于已经耗尽。
        每个账号换周期后停止自己的提醒，关闭开关停止整轮提醒；其他尚未换周期的账号继续检查。监控暂停时不自动发送，投递失败可在通知记录查看；本提醒不会替你使用重置卡。
      </p>
    </section>
    <section>
      <h3 class="text-lg font-semibold">管理员手动调整当前周期结转</h3>
      <p class="mt-3 text-sm leading-7">
        在“额度分配”页面，有非零结转的单元格会在合同份额输入框右侧显示“结转权益”。这是当前周期的独立加减项，不是新的合同份额。
        例如合同 50%、结转 +17 个百分点，本期可用权益为 67%；把结转改为
        10，本期变为 60%；改为 0 则清除结转，保存后额外输入框隐藏。
      </p>
      <p class="mt-3 text-sm leading-7">
        支持 −100 至 100，最多 5
        位小数；正数为补偿，负数为扣除。点击“保存分配”才生效。
        同池多个账号的结转分别标明账号，不能把不同账号的百分点直接合并。手动调整不会自动修改他人的结转，因此可能打破原本零和的分配，需要管理员自行协调。
      </p>
      <p class="mt-3 text-sm leading-7 opacity-75">
        仅管理员可修改当前周期，已结算历史不会重写。周期切换、绑定变化或其他管理员先修改后，旧页面的保存会被拒绝，需要刷新重试。
        修改会更新余额建议，不直接修改上游余额；仍按原来的自动或手动应用方式执行。爽蹬卡片的周期详情中可查看管理员、时间和修改前后数值。
      </p>
    </section>
    <section>
      <h3 class="text-lg font-semibold">切换例子，看看下一周期怎么分</h3>
      <fieldset class="mt-4 fieldset">
        <legend class="fieldset-legend">示例结算方式</legend>
        <div class="flex gap-5">
          <label class="label cursor-pointer"
            ><input
              v-model="carryoverEnabled"
              class="radio radio-sm"
              type="radio"
              name="tutorial-burst-mode"
              :value="true"
            />结转模式</label
          >
          <label class="label cursor-pointer"
            ><input
              v-model="carryoverEnabled"
              class="radio radio-sm"
              type="radio"
              name="tutorial-burst-mode"
              :value="false"
            />不结转模式</label
          >
        </div>
      </fieldset>
      <div class="mt-4 flex flex-wrap gap-2">
        <button
          v-for="(item, index) in examples"
          :key="item.title"
          type="button"
          class="btn btn-sm"
          :class="selected === index ? 'btn-primary' : 'btn-outline'"
          :aria-pressed="selected === index"
          @click="selected = index"
        >
          {{ item.title }}
        </button>
      </div>
      <TemporaryBurstExampleComic
        :rows="rows"
        :carryover-enabled="carryoverEnabled"
        :note="
          carryoverEnabled
            ? example.note
            : '不结转模式：本轮多用不追账、少用不补偿，下一周期仍按原合同。'
        "
      />
    </section>
    <section>
      <h3 class="text-lg font-semibold">结算规则：只结转借用，不结转闲置</h3>
      <div class="mt-4 rounded-box bg-base-100 p-5 font-mono text-sm leading-8">
        <p>仅适用于结转模式；不结转或手动提前终止的轮次不产生新调整。</p>
        <p>本期可用权益 = 固定合同权益 + 上期结转调整</p>
        <p>未用份额 = max(本期可用权益 − 实际归属, 0)</p>
        <p>超用份额 = max(实际归属 − 本期可用权益, 0)</p>
        <p>补偿总额 = min(总超用份额, 总未用份额)</p>
        <p>补偿按各人的未用份额比例分摊；扣除按超用份额比例分摊。</p>
      </div>
      <p class="mt-3 text-sm leading-7 opacity-75">
        自动结算的加减始终守恒，管理员手动调整不受此约束。数据以 5
        位小数的百分点记账，展示保留 2
        位；未分配或无法归属的额度不会被虚构成某人的借出份额。周期记录独立保存，合同和真实用量不被改写。
      </p>
    </section>
    <section>
      <h3 class="text-lg font-semibold">
        再下个周期，不会永久变成 67% / 17% / 16%
      </h3>
      <p class="mt-3 text-sm leading-7 opacity-75">
        基础合同始终是 50% / 25% / 25%。第二周期按调整后权益 67% / 17% / 16%
        使用完，借用账结清，第三周期回到基础合同；若第二周期使用 60% / 20% /
        20%，仍有 +7 / −3 / −4 的差额，第三周期为 57% / 22% /
        21%。如果没有人超过本期调整后权益，未使用且未被借用的部分到期作废，不累计成无限额度。
      </p>
    </section>
    <section>
      <h3 class="text-lg font-semibold">多账号、结算依据与安全边界</h3>
      <ul
        class="mt-3 list-inside list-disc space-y-3 text-sm leading-7 opacity-75"
      >
        <li>
          这是全局模式，不只作用于首页当前选择的账号。同一个 Sub2API
          用户只有一个全局钱包，涉及所有启用的 Sub2API 账号；CPA 不参与。
        </li>
        <li>
          多账号开启前会额外警告：共享余额无法按账号隔离。按账号分别记账结算，不代表某个账号的消费被单独限制。
        </li>
        <li>
          爽蹬原周期内采样间隔减半；最后观测已用 ≥ 90% 或距原定重置 ≤ 30
          分钟时，每分钟采样。各账号确认换周期后分别恢复常规间隔；全局退出不提前取消其他旧周期账号的加速。
        </li>
        <li>
          任何账号首次确认换周期，或到达开启时最早的重置时间，就停止统一建议
          9999。其他账号仍在各自原周期结束后结算；不会为了等待较晚账号而让新周期继续爽蹬。
        </li>
        <li>
          每个账号独立以周期百分比记账，再按现有容量与实际扣费换算汇入全局余额建议。不同账号的百分点不直接混在一起相减。
        </li>
        <li>
          开启时冻结本期合同和参与者绑定，以有效观测的归属百分比结算整轮，而不是只算开启后的请求。人工测算起点、价格时期变化不当成订阅换周期。
        </li>
        <li>
          结转使用旧周期最后保留的有效归属证据，不猜测未采样的尾部消费。缺失参与者证据、欠账身份变化或跨过多个周期时保留待结算错误，不静默丢账；车主也可提前终止本轮并放弃后续结转。
        </li>
        <li>
          请保持监控运行。后台暂停或上游写入失败时，建议变化不等于余额已修改；需要恢复采样并核对实际应用结果。上一轮仍有原周期待结算时不能重复开启。
        </li>
      </ul>
    </section>
  </div>
</template>
