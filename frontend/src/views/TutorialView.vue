<script setup lang="ts">
import PageShellHeader from "@/components/common/PageShellHeader.vue";

const steps = [
  {
    title: "连接 Sub2API",
    icon: "code-bracket",
    text: "进入系统设置，填写容器可访问的 Sub2API 地址和 Admin Token，再从系统自动读取的列表中选择实际承载套餐的 OpenAI 上游账号。保持默认“被动查询”即可避免为了查额度而额外请求 OpenAI 官方接口。",
  },
  {
    title: "添加拼车参与者",
    icon: "user-group",
    text: "进入参与者页面，从 Sub2API 用户列表中分别选择自己和车友，再填写约定的周限权益比例。所有启用参与者的比例合计不能超过 100%。",
  },
  {
    title: "创建只读系统用户",
    icon: "user-plus",
    text: "需要让车友自行查看时，由管理员进入系统用户页面创建登录账号，并绑定一个或多个参与者。普通用户只能进入额度统计页面；周限等效额度估算保持可见，参与者账号用量只展示其绑定范围。",
  },
  {
    title: "完成首次测算",
    icon: "calculator",
    text: "先让上游账号产生一次正常业务请求，使 Sub2API 保存七天额度快照，然后在首页点击“立即测算”。首次测算会读取本周期总用量和每个 Sub2API 用户的用量，自动把此前已经消耗的百分比归属给实际使用者。",
  },
  {
    title: "按建议手动调整额度",
    icon: "clipboard-document-check",
    text: "首页会用自然语言说明每个 Sub2API 账号应设置的用户余额。核对账号用量、剩余权益和原因后，到 Sub2API 管理台手动修改用户余额；本服务绝不会自动修改任何数据。",
  },
  {
    title: "查看趋势和安全记录",
    icon: "presentation-chart-line",
    text: "额度统计页面会并列展示“本周期累计折算”和“今日用量折算”。今日观测跨过的整数周限不足设置阈值时会明确显示样本不足，不会用短增量给出误导结论；下方历史图可按天、按月查看，并可切换周期累计收盘估算与各日首末观测的日内增量估算。",
  },
];
</script>

<template>
  <PageShellHeader>
    <div class="grow">
      <div class="breadcrumbs text-sm">
        <ul>
          <li><RouterLink to="/">帮助</RouterLink></li>
          <li><h1>使用教程</h1></li>
        </ul>
      </div>
    </div>
    <RouterLink to="/settings" class="btn btn-primary btn-sm">
      <AppIcon name="cog-6-tooth" class="size-4" />开始配置
    </RouterLink>
  </PageShellHeader>

  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <div>
        <h2 class="card-title">
          <AppIcon name="book-open" class="size-5" />从零开始
        </h2>
        <p class="mt-2 max-w-3xl text-sm leading-6 opacity-70">
          这个服务负责读取 Sub2API
          数据、维护百分比权益账本、计算人工额度建议并发送提醒；实际额度修改始终由管理员在
          Sub2API 中完成。
        </p>
      </div>
      <ol class="grid gap-4 lg:grid-cols-2">
        <li
          v-for="(step, index) in steps"
          :key="step.title"
          class="rounded-box border border-base-300 bg-base-100 p-5"
        >
          <div class="flex items-center gap-3">
            <span class="badge badge-neutral">{{ index + 1 }}</span>
            <AppIcon :name="step.icon" class="size-5 opacity-60" />
            <h3 class="font-semibold">{{ step.title }}</h3>
          </div>
          <p class="mt-3 text-sm leading-6 opacity-70">{{ step.text }}</p>
        </li>
      </ol>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body gap-3">
      <h2 class="card-title">
        <AppIcon name="clock" class="size-5" />采集与校准
      </h2>
      <p class="text-sm leading-6 opacity-70">
        “本地探测间隔”控制读取 Sub2API
        本地统计的频率。只有成本进度达到阈值、参与者额度接近耗尽、活跃时间过长、临近重置或手动测算时，系统才读取新的上游百分比快照。
      </p>
      <p class="text-sm leading-6 opacity-70">
        自动探测由容器内的 Django
        后台进程执行，不依赖浏览器保持打开。间隔按每轮开始时间对齐；“校准历史”只记录形成上游快照的轮次，因此记录时间不会固定等于本地探测间隔。
      </p>
      <p class="text-sm leading-6 opacity-70">
        图表的“每次探测／每小时／每天”只改变展示精度，不会提高实际请求频率。
      </p>
      <p class="text-sm leading-6 opacity-70">
        “账号本周期用量”来自 Sub2API
        用量日志，按所选上游账号和参与者聚合，用于归属百分比权益；“用户余额”来自
        Sub2API 用户详情，是该用户所有平台和上游账号共用的全局余额。
        为避免其他业务干扰建议，拼车参与者最好使用专用 Sub2API 用户。
      </p>
      <p class="text-sm leading-6 opacity-70">
        启用“FAST 修正”后，系统会只读每个成功采样区间内的请求日志，识别 FAST
        请求并补足 Sub2API
        与上游套餐之间的倍率差异。关闭只会停止新修正并隐藏观测列，不会删除已有结果；后续重新启用时，可从当前周期或全部历史执行修正重建。
      </p>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body gap-3">
      <h2 class="card-title">
        <AppIcon name="envelope" class="size-5" />邮件提醒
      </h2>
      <p class="text-sm leading-6 opacity-70">
        系统设置支持传统 SMTP 和
        Resend。选择一种服务，填写发件人与接收邮箱并发送测试邮件。额度耗尽、估算变化和采集失败可分别控制是否通知。
      </p>
      <p class="text-sm leading-6 opacity-70">
        Resend 需要已验证的发件域名；API Key 和 SMTP 密码都会加密保存。
      </p>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body gap-3">
      <h2 class="card-title">
        <AppIcon name="scale" class="size-5" />中途开始拼车
      </h2>
      <p class="text-sm leading-6 opacity-70">
        权益比例始终填写双方约定的总周限份额，而不是当前剩余份额。例如上游已经由车主用掉
        10%，现在双方约定各占 50%，仍然给车主和车友各填
        50%。只要此前用量记录在车主所选的 Sub2API 用户下面，首次测算会把已用的
        10% 全部归属给车主，结果就是车主剩余 40%、车友剩余 50%。
      </p>
      <p class="text-sm leading-6 opacity-70">
        使用“平均恒定”模型时，上游显示的整数 p% 按截尾结果处理，真实进度区间取
        p% 到
        p+0.9%。系统分别用区间两端反推总容量，再扣除参与者本周期已用成本，因而余额建议显示为“最低值
        ~
        最高值”；首页一键设置采用两端的中间值。“时变额度”仍显示单一建议值，额度统计中的“本周期累计折算”也始终保留单一数值。
      </p>
      <p class="text-sm leading-6 opacity-70">
        如果历史请求无法对应到已添加的 Sub2API
        用户，无法可靠猜测使用者；首页会把这部分显示为“未归属的已用周限”，需要先检查用户映射。
      </p>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body gap-3">
      <h2 class="card-title">
        <AppIcon name="arrow-path" class="size-5" />周限刷新
      </h2>
      <p class="text-sm leading-6 opacity-70">
        系统不保存独立的周期账本，而是从原始采样推导区间。管理员指定起点的优先级最高；否则使用上游
        reset_at 减去七天得到官方起点。同一 reset_at
        下的百分比回退只会作为异常记录自动排除，不会因连续出现低值就擅自建立新区间。确认发生官方赠送刷新时，可在“观测记录”中把对应观测设为管理员起点。
        新增观测只计算自身；排除、恢复或取消起点时，仅从最早受影响的区间向后重算，更早区间保持不变。新边界形成有效样本前沿用上一段最终估值；只有账号从未形成有效历史样本时，才使用设置中的“无样本时美元
        / 1%”。
      </p>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body gap-3">
      <h2 class="card-title">
        <AppIcon name="finger-print" class="size-5" />登录安全
      </h2>
      <p class="text-sm leading-6 opacity-70">
        每次成功和失败登录都会记录时间、用户名、服务端来源
        IP、直连地址、浏览器信息和可用的 WebRTC IP。WebRTC
        数据来自浏览器，可能被隐藏或伪造，不能代替服务端来源 IP。
      </p>
      <p class="text-sm leading-6 opacity-70">
        如果前面有反向代理，只能通过 Docker 环境变量 TRUSTED_PROXY_COUNT
        配置实际可信代理层数，不能直接信任任意客户端发送的代理头。
      </p>
    </div>
  </section>

  <section class="card col-span-12 bg-base-200 shadow-xs xl:col-span-6">
    <div class="card-body gap-3">
      <h2 class="card-title">
        <AppIcon name="exclamation-triangle" class="size-5" />常见问题
      </h2>
      <ul class="list-disc space-y-2 pl-5 text-sm leading-6 opacity-70">
        <li>提示没有被动快照：先通过该 OpenAI 上游账号产生一次正常请求。</li>
        <li>统计图为空：新部署不会反推历史，需要等待本地探测和有效测算。</li>
        <li>
          建议金额变化：周限每 1% 对应的美元价值会浮动，这是系统持续校准的核心。
        </li>
        <li>
          数据库迁移：在系统设置中导出 SQLite 备份；新服务器导入后还要沿用原来的
          DJANGO_SECRET_KEY，才能解密敏感设置。
        </li>
        <li>
          邮件测试失败：检查服务类型、发件人验证状态、密钥、端口和加密方式。
        </li>
      </ul>
    </div>
  </section>
</template>
