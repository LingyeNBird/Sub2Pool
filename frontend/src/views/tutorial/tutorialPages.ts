export type TutorialNoteTone = "info" | "warning" | "success";

export interface TutorialNote {
  title: string;
  text: string;
  tone: TutorialNoteTone;
}

export interface TutorialSection {
  title: string;
  paragraphs?: string[];
  steps?: string[];
  bullets?: string[];
  notes?: TutorialNote[];
}

export interface TutorialPage {
  id: string;
  group: string;
  title: string;
  summary: string;
  icon: string;
  sections: TutorialSection[];
  action?: {
    label: string;
    to: string;
  };
}

export interface TutorialGroup {
  label: string;
  pages: TutorialPage[];
}

export const tutorialGroups: TutorialGroup[] = [
  {
    label: "开始使用",
    pages: [
      {
        id: "overview",
        group: "开始使用",
        title: "产品概览",
        summary:
          "了解 Sub2Pool 解决什么问题，以及完成一次额度测算所需的最短操作路径。",
        icon: "book-open",
        sections: [
          {
            title: "系统职责",
            paragraphs: [
              "本服务只读 Sub2API 的账号快照与用量日志，维护参与者的百分比权益账本，并把动态周限换算成可执行的美元余额建议。",
              "额度只有在管理员前往 Sub2API 手动调整，或在建议卡片中明确点击“一键设置”后才会改变。后台监控不会自行修改用户余额。",
            ],
          },
          {
            title: "首次使用流程",
            steps: [
              "连接 Sub2API，并选择实际承载套餐的 OpenAI 上游账号。",
              "添加车主和车友，填写双方约定的完整周期权益比例。",
              "如需让车友查看数据，创建普通系统用户并绑定参与者。",
              "让上游账号产生正常业务请求，再执行首次测算。",
              "核对首页建议，到 Sub2API 手动调整，或明确使用一键设置。",
            ],
          },
          {
            title: "推荐阅读顺序",
            bullets: [
              "第一次部署：依次阅读“连接 Sub2API”“参与者与系统用户”“首次测算”。",
              "日常管理：重点阅读“额度建议”“采样与校准”“统计与模型”。",
              "服务器迁移或异常处理：阅读“通知与登录安全”“数据维护与排错”。",
            ],
          },
        ],
        action: { label: "前往系统设置", to: "/settings" },
      },
      {
        id: "connection",
        group: "开始使用",
        title: "连接 Sub2API",
        summary:
          "配置服务地址与 Admin Token，读取上游账号，并选择安全的额度查询方式。",
        icon: "code-bracket",
        sections: [
          {
            title: "准备连接信息",
            steps: [
              "填写当前容器能够访问的 Sub2API 地址。Sub2API 在宿主机运行时，Docker Desktop 通常可使用 host.docker.internal。",
              "填写 Sub2API Admin Token。Token 只保存在服务端，不会发送到浏览器之外的第三方。",
              "点击“读取账号”，从返回列表中选择实际承载套餐的 OpenAI 上游账号。",
              "点击“测试连接”确认地址、Token 和账号可用，然后保存连接设置。",
            ],
            notes: [
              {
                tone: "info",
                title: "测试连接无需先保存",
                text: "测试会直接使用表单里当前填写的地址和 Token，不会要求先覆盖数据库中的现有设置。",
              },
            ],
          },
          {
            title: "选择查询方式",
            paragraphs: [
              "默认使用“被动查询”：只读取 Sub2API 已保存的额度快照，不会为了查询而额外调用 OpenAI 官方额度接口。快照会在 Sub2API 正常转发业务请求时更新。",
              "只有明确接受额外官方请求及其潜在风控影响时，才应切换为主动查询。日常拼车监控建议保持被动查询。",
            ],
          },
          {
            title: "连接成功后",
            bullets: [
              "首页不再显示连接配置警告。",
              "参与者表单可以从 Sub2API 用户列表中选择账号。",
              "产生过正常请求后，立即测算可以读取七天窗口百分比快照。",
            ],
          },
        ],
        action: { label: "配置 Sub2API", to: "/settings" },
      },
      {
        id: "participants",
        group: "开始使用",
        title: "参与者与系统用户",
        summary:
          "把 Sub2API 用户映射为拼车参与者，并按合同约定填写完整周期权益。",
        icon: "user-group",
        sections: [
          {
            title: "添加参与者",
            steps: [
              "进入参与者页面，添加车主和每一位车友。",
              "从 Sub2API 用户列表选择对应账号，不要手工猜测用户 ID。",
              "填写合同权益比例。所有启用参与者的权益总和不得超过 100%。",
              "按需填写备注，并确认需要参与后台监控的账号已经启用。",
            ],
          },
          {
            title: "权益比例怎么填",
            paragraphs: [
              "权益始终填写双方约定的完整周期份额，而不是添加参与者时的剩余份额。例如上游已经由车主使用 10%，此时双方约定各占 50%，仍应分别填写 50%。",
              "只要此前用量记录在车主对应的 Sub2API 用户下面，首次测算就会把已用部分归属给车主，得到车主剩余 40%、车友剩余 50% 的结果。",
            ],
            notes: [
              {
                tone: "warning",
                title: "无法映射的历史用量",
                text: "如果历史请求不能对应到已添加的 Sub2API 用户，系统不会猜测使用者，而会显示为未归属用量。",
              },
            ],
          },
          {
            title: "创建普通系统用户",
            paragraphs: [
              "需要让车友自行查看时，由管理员在“系统用户”页面创建登录账号，并绑定一个或多个参与者。",
              "普通用户只能查看参与者和额度统计页面；参与者页面固定使用只读卡片，只显示其绑定账号，无法编辑或删除。",
            ],
          },
        ],
        action: { label: "管理参与者", to: "/participants" },
      },
      {
        id: "first-measurement",
        group: "开始使用",
        title: "首次测算",
        summary:
          "让系统获得第一份有效快照，建立当前周期的容量估计与参与者归属。",
        icon: "calculator",
        sections: [
          {
            title: "测算前提",
            paragraphs: [
              "被动查询不会主动请求 OpenAI。请先通过所选上游账号产生一次正常业务请求，让 Sub2API 保存新的七天额度快照。",
              "参与者也必须已经绑定正确的 Sub2API 用户，否则逐用户用量无法形成可靠归属。",
            ],
          },
          {
            title: "执行首次测算",
            steps: [
              "回到额度总览，确认连接警告已经消失。",
              "点击“立即测算”，等待服务读取上游百分比、本周期总成本和逐用户用量。",
              "进入观测记录，确认新增记录的查询方式、上游百分比和成本数据符合预期。",
              "返回额度总览查看容量结论与需要调整的参与者。",
            ],
          },
          {
            title: "没有立即形成结论",
            bullets: [
              "没有被动快照：先产生一笔正常请求，再重新测算。",
              "百分比没有变化：探测仍会执行，但可能不会形成新的有效校准样本。",
              "只有一条观测：系统会使用历史软先验或无历史初始化范围，后续样本会逐步收紧结论。",
            ],
          },
        ],
        action: { label: "查看额度总览", to: "/" },
      },
    ],
  },
  {
    label: "日常使用",
    pages: [
      {
        id: "recommendations",
        group: "日常使用",
        title: "额度建议与调整",
        summary:
          "理解首页建议的含义，并选择手动调整、跳转管理台或明确执行一键设置。",
        icon: "clipboard-document-check",
        sections: [
          {
            title: "阅读当前建议",
            paragraphs: [
              "首页只显示当前确实需要调整的参与者。建议文字会给出 Sub2API 用户标识、当前余额、建议余额和本周期用量。",
              "点击“保守美元 / 1%”或其他可计算指标的“查看依据”，可以查看计算起点、终点、区间和公式。直接来自上游的原始百分比不会伪装成计算结果。",
            ],
          },
          {
            title: "执行调整",
            steps: [
              "点击整条建议卡片打开操作窗口。",
              "选择“跳转至 Admin API”可打开已配置的 Sub2API 管理地址，由管理员手动修改。",
              "选择“一键设置”会使用服务端 Admin Token，只把该用户余额更新为当前建议值。",
              "成功后该建议暂时显示完成状态；重新进入首页时，页面会以最新数据重新判断是否还需调整。",
            ],
            notes: [
              {
                tone: "warning",
                title: "一键设置不是自动托管",
                text: "它只响应管理员当前这一次明确点击，不会建立后续自动调额任务。",
              },
            ],
          },
          {
            title: "权益耗尽与偏差",
            bullets: [
              "参与者余额耗尽但仍有权益时，系统继续给出补充建议，并可按通知规则发送邮件。",
              "已确认超过合同权益时，参与者页面显示“权益偏差”；后续容量估计改变后，偏差会按最新结果重新计算，不会永久锁死。",
              "当其他参与者权益均耗尽、只剩最后一人时，剩余权益归该参与者，建议不再额外扣安全系数。",
            ],
          },
        ],
        action: { label: "查看当前建议", to: "/" },
      },
      {
        id: "collection",
        group: "日常使用",
        title: "采样与校准",
        summary: "理解本地探测、上游快照和有效校准记录之间的区别。",
        icon: "clock",
        sections: [
          {
            title: "后台如何运行",
            paragraphs: [
              "自动探测由容器内的 Django 后台任务执行，不依赖浏览器保持打开。页面倒计时只是展示下一轮计划时间，不负责发送采样请求。",
              "本地探测间隔按每轮开始时间对齐。一次探测完成后，下一轮仍依据后台计划运行，不会因为关闭网页而停止。",
            ],
          },
          {
            title: "为什么探测了却没有新记录",
            paragraphs: [
              "后台每轮都会先读取 Sub2API 本地统计。只有成本进度达到阈值、参与者接近耗尽、活跃时间过长、临近重置、强制读取窗口到期或管理员手动测算时，才需要读取新的上游百分比快照。",
              "校准历史记录的是形成有效上游快照的轮次，不是每一次本地探测。因此记录间隔可能大于设置中的本地探测间隔。",
            ],
            notes: [
              {
                tone: "info",
                title: "图表精度不会改变采样频率",
                text: "“每次探测／每小时／每天”只改变图表聚合方式，实际采样频率仍由系统设置控制。",
              },
            ],
          },
          {
            title: "FAST 修正",
            paragraphs: [
              "启用 FAST 修正后，系统会只读成功采样区间内的请求日志，识别 FAST 请求并补足 Sub2API 与上游套餐之间的倍率差异。",
              "关闭 FAST 修正只会停止新修正并隐藏相关观测列，不会删除已有结果。重新启用后可以按需重建当前周期或全部历史。",
            ],
          },
        ],
        action: { label: "查看观测记录", to: "/observations" },
      },
      {
        id: "statistics",
        group: "日常使用",
        title: "统计、模型与粒子轨迹",
        summary: "区分简单端点折算、动态额度建议、API 用量构成和粒子滤波轨迹。",
        icon: "presentation-chart-line",
        sections: [
          {
            title: "额度统计页面",
            paragraphs: [
              "“本周期累计折算”使用周期起点和当前观测的成本、整数百分比做端点计算；“今日用量折算”只使用今天已经覆盖的观测区间。两者都是便于理解的简单公式。",
              "今日百分比跨度小于设置阈值时，页面会明确显示样本不足，避免把整数百分比的短区间误差放大成结论。点击可计算指标可以打开计算依据。",
            ],
          },
          {
            title: "参与者账号用量",
            bullets: [
              "趋势图按 Sub2API 用量日志展示每个参与者的周期用量。",
              "“API 用量构成”按当前周期统计各 API Key 占参与者用量和总周限的比例。",
              "普通系统用户只能看到自己绑定的参与者。",
            ],
          },
          {
            title: "建议模型与粒子轨迹",
            paragraphs: [
              "“平均恒定”按周期累计成本和整数百分比形成建议；“时变额度”会估计连续容量路径、整数显示规则和参与者归属，并给出概率区间与确定性边界。",
              "粒子轨迹页面用于解释时变额度模型：路径表示容量随观测变化的估计，点云表示仍可能成立的候选状态，搜索上下限显示当前粒子探索范围；页面顶部可切换历史周期并重放当时的完整轨迹。统计页的简单端点折算不会改用粒子滤波。",
            ],
          },
        ],
        action: { label: "查看额度统计", to: "/statistics" },
      },
    ],
  },
  {
    label: "机制与维护",
    pages: [
      {
        id: "cycles",
        group: "机制与维护",
        title: "周限刷新与中途拼车",
        summary: "处理官方周期刷新、福利刷新、管理员起点和周期中途加入。",
        icon: "arrow-path",
        sections: [
          {
            title: "周期起点怎么确定",
            paragraphs: [
              "管理员指定的起点优先级最高；没有管理员起点时，系统使用上游 reset_at 减去七天推导官方周期起点。",
              "同一 reset_at 下的百分比回退会作为异常观测排除，不会仅因为连续低值就擅自建立新周期。确认发生官方赠送刷新时，可在观测记录中把对应观测设为管理员起点。",
            ],
          },
          {
            title: "刷新后的估计",
            paragraphs: [
              "新增观测会从当前归属区间的起点开始重建。排除、恢复或取消起点时，只从最早受影响区间向后重算，更早区间保持不变。",
              "正常新周期会沿用上一周期最终容量估计作为软先验；只有账号从未形成历史时，才从完整搜索范围初始化。",
            ],
          },
          {
            title: "周期中途开始拼车",
            paragraphs: [
              "参与者权益仍填写合同约定的完整周期比例，不需要手工扣掉加入前已经使用的部分。系统会根据加入前的逐用户历史用量，把既有消耗归属给实际使用者。",
            ],
            notes: [
              {
                tone: "warning",
                title: "先确认历史用户映射",
                text: "加入前的请求必须能映射到正确的 Sub2API 用户；无法映射的部分只能保持未归属。",
              },
            ],
          },
        ],
        action: { label: "管理周期观测", to: "/observations" },
      },
      {
        id: "notifications-security",
        group: "机制与维护",
        title: "通知与登录安全",
        summary: "配置 SMTP 或 Resend，并使用登录审计和地址封禁保护管理页面。",
        icon: "shield-check",
        sections: [
          {
            title: "邮件服务",
            paragraphs: [
              "系统支持传统 SMTP 和 Resend。选择一种服务，填写发件人与接收邮箱并发送测试邮件，再分别启用额度耗尽、建议变化、折算变化或采集失败通知。",
              "Resend 需要经过验证的发件域名；Resend API Key 和 SMTP 密码都会加密保存。",
            ],
          },
          {
            title: "登录记录",
            paragraphs: [
              "每次成功和失败登录都会记录时间、用户名、服务端来源 IP、直连地址、浏览器信息和可获得的 WebRTC IP。WebRTC 数据由浏览器上报，可能被隐藏或伪造，不能替代服务端来源 IP。",
              "登录记录中的服务器来源 IP、直连地址和 WebRTC IP 均可加入封禁列表。服务端能够在收到请求时识别的地址会直接得到空响应；WebRTC 地址只能在浏览器完成探测后限制登录并保持页面空白。",
            ],
          },
          {
            title: "反向代理边界",
            paragraphs: [
              "如果服务前面有反向代理，只能通过 Docker 环境变量 TRUSTED_PROXY_COUNT 配置真实可信的代理层数。不要直接信任任意客户端提交的代理头。",
            ],
          },
        ],
        action: { label: "查看登录记录", to: "/login-records" },
      },
      {
        id: "maintenance",
        group: "机制与维护",
        title: "数据维护与排错",
        summary: "补全旧观测、重建派生结果、迁移数据库，并处理常见空数据问题。",
        icon: "wrench-screwdriver",
        sections: [
          {
            title: "历史补全与重建",
            paragraphs: [
              "旧版本观测可能缺少逐用户用量。进入“系统设置 → 数据维护”先检查历史缺口，再只读 Sub2API 请求日志完成补全。",
              "补全成功后系统会从第一条观测重新生成派生结果；原始百分比、成本和观测时间不会改变。单独执行“重建全部结果”不会重新请求 Sub2API，只会使用数据库现有原始数据。",
            ],
          },
          {
            title: "数据库迁移",
            steps: [
              "在系统设置中导出完整 SQLite 备份。",
              "在新服务器部署相同或更新版本，并配置与旧服务器相同的 DJANGO_SECRET_KEY。",
              "导入备份，等待服务完成恢复，然后重新登录核对连接、参与者和观测记录。",
            ],
            notes: [
              {
                tone: "warning",
                title: "必须保留 DJANGO_SECRET_KEY",
                text: "Admin Token、邮件密钥等敏感配置依赖它加密。更换后，导入数据库中的加密字段将无法解密。",
              },
            ],
          },
          {
            title: "常见问题",
            bullets: [
              "提示没有被动快照：通过所选 OpenAI 上游账号产生一次正常请求。",
              "统计图为空：新部署不会凭空生成历史，需要等待本地探测和有效测算。",
              "建议金额持续变化：周限每 1% 对应的美元价值本来就会浮动。",
              "邮件测试失败：检查服务类型、发件人验证状态、密钥、端口和加密方式。",
              "自动探测没有新增校准记录：确认是否达到进度阈值或其他强制读取条件。",
            ],
          },
        ],
        action: { label: "打开数据维护", to: "/settings" },
      },
    ],
  },
];

export const tutorialPages = tutorialGroups.flatMap((group) => group.pages);
