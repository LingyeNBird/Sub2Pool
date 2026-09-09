<script setup lang="ts">
import { computed, ref } from "vue";
const examples = [
  {
    title: "整轮用满",
    usage: [33, 33, 34],
    note: "B 借用 8 个百分点，C 借用 9 个百分点；A 让出了 17 个百分点，全部补偿给 A。",
  },
  {
    title: "B 超用 5 个百分点",
    usage: [20, 30, 10],
    note: "A 剩余 30、C 剩余 15，比例 2∶1；只把 B 超用的 5 个百分点按此比例补偿，而不是结转所有剩余。",
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
];
const selected = ref(0);
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
  const borrowed = Math.min(spare, excess);
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
const signed = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
</script>

<template>
  <div class="space-y-8 py-7">
    <section>
      <h3 class="text-lg font-semibold">先放开使用，再结算借用</h3>
      <p class="mt-3 text-sm leading-7 opacity-75">
        “爽蹬”只放开本期钱包余额限制，不改变合同份额，也不会增加套餐总容量。多人暂时按需使用，期末用百分比权益结算，避免把不同模型的美元价格直接当作公平分配比例。
      </p>
      <ul class="steps steps-vertical mt-5 w-full lg:steps-horizontal">
        <li class="step step-primary">管理员开启</li>
        <li class="step step-primary">建议余额 9999</li>
        <li class="step step-primary">正常记录消耗</li>
        <li class="step step-primary">首个换周期统一退出</li>
        <li class="step step-primary">各账号分别结转</li>
      </ul>
    </section>
    <section>
      <h3 class="text-lg font-semibold">从首页开启</h3>
      <ol class="mt-3 list-inside list-decimal space-y-3 text-sm leading-7">
        <li>
          进入“额度总览”，在“当前额度建议”下方找到“临时爽蹬”。确保所有启用的
          Sub2API 账号都有当前周期的有效观测和完整参与者归属。
        </li>
        <li>点击“开启临时爽蹬”，阅读全局影响并确认。仅管理员可以开启。</li>
        <li>
          已开启自动应用建议时，立即尝试设置所有参与者余额为
          9999；未开启时，余额不会直接变化，需要从额度建议手动应用。
        </li>
        <li>
          新周期不再建议
          9999，恢复普通建议并计入权益结转；未开启自动应用时，也需要手动应用新的普通建议，把上游高余额收回来。
        </li>
      </ol>
      <p class="mt-4 alert text-sm alert-warning">
        9999 是余额，不是 9999%
        权益。它可能让一个人迅速耗尽全组订阅额度；上游自身的限速、余额权限和套餐限制仍然有效。
      </p>
    </section>
    <section>
      <h3 class="text-lg font-semibold">切换例子，看看下一周期怎么分</h3>
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
      <p class="mt-4 text-sm leading-7">{{ example.note }}</p>
      <div
        class="mt-4 overflow-x-auto rounded-box border border-base-300 bg-base-100 p-3"
      >
        <svg
          viewBox="0 0 760 260"
          role="img"
          :aria-label="`${example.title}：本期消耗与下期权益对比`"
          class="w-full min-w-[570px] text-base-content"
        >
          <text x="95" y="25" fill="currentColor" font-size="15">
            本期实际使用
          </text>
          <text x="445" y="25" fill="currentColor" font-size="15">
            下一周期可用权益
          </text>
          <g
            v-for="(row, index) in rows"
            :key="row.name"
            :transform="`translate(0 ${55 + index * 65})`"
          >
            <text
              x="15"
              y="23"
              fill="currentColor"
              font-size="18"
              font-weight="600"
            >
              {{ row.name }}
            </text>
            <rect
              x="70"
              y="0"
              width="240"
              height="34"
              rx="5"
              fill="currentColor"
              opacity=".08"
            />
            <rect
              x="70"
              y="0"
              :width="row.used * 2.4"
              height="34"
              rx="5"
              fill="var(--color-info)"
            />
            <text x="78" y="23" fill="currentColor" font-size="15">
              {{ row.used }}%
            </text>
            <text x="333" y="23" fill="currentColor" font-size="15">
              {{ signed(row.change) }} →
            </text>
            <rect
              x="445"
              y="0"
              width="240"
              height="34"
              rx="5"
              fill="currentColor"
              opacity=".08"
            />
            <rect
              x="445"
              y="0"
              :width="row.next * 2.4"
              height="34"
              rx="5"
              fill="var(--color-primary)"
              opacity=".7"
            />
            <text x="453" y="23" fill="currentColor" font-size="15">
              {{ row.next.toFixed(2) }}%
            </text>
          </g>
          <text x="70" y="250" fill="currentColor" font-size="13">
            固定合同 A 50% / B 25% / C 25%；箭头是权益百分点调整，不是美元。
          </text>
        </svg>
      </div>
      <div class="mt-4 overflow-x-auto">
        <table class="table table-sm">
          <thead>
            <tr>
              <th>参与者</th>
              <th>合同权益</th>
              <th>本期使用</th>
              <th>下期调整</th>
              <th>下期权益</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td>{{ row.name }}</td>
              <td>{{ row.share }}%</td>
              <td>{{ row.used }}%</td>
              <td>{{ signed(row.change) }} 个百分点</td>
              <td>{{ row.next.toFixed(2) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section>
      <h3 class="text-lg font-semibold">结算规则：只结转借用，不结转闲置</h3>
      <div class="mt-4 rounded-box bg-base-100 p-5 font-mono text-sm leading-8">
        <p>本期可用权益 = 固定合同权益 + 上期结转调整</p>
        <p>未用份额 = max(本期可用权益 − 实际归属, 0)</p>
        <p>超用份额 = max(实际归属 − 本期可用权益, 0)</p>
        <p>补偿总额 = min(总超用份额, 总未用份额)</p>
        <p>补偿按各人的未用份额比例分摊；扣除按超用份额比例分摊。</p>
      </div>
      <p class="mt-3 text-sm leading-7 opacity-75">
        加减始终守恒。数据以 5 位小数的百分点记账，展示保留 2
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
          结算使用旧周期最后保留的有效证据，不猜测未采样的尾部消费；卡片显示证据时间。缺失参与者证据、欠账身份变化或跨过多个周期时保留待结算错误，不静默丢账。
        </li>
        <li>
          请保持监控运行。后台暂停或上游写入失败时，建议变化不等于余额已修改；需要恢复采样并核对实际应用结果。上一轮仍有原周期待结算时不能重复开启。
        </li>
      </ul>
    </section>
  </div>
</template>
