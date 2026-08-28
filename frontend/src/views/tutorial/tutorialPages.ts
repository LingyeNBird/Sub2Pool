export type TutorialNoteTone = "info" | "warning" | "success";

export interface TutorialNote {
  title: string;
  text: string;
  tone: TutorialNoteTone;
}

export interface TutorialCodeBlock {
  title?: string;
  language?: string;
  code: string;
}

export interface TutorialSection {
  title: string;
  paragraphs?: string[];
  steps?: string[];
  bullets?: string[];
  notes?: TutorialNote[];
  codeBlocks?: TutorialCodeBlock[];
}

export interface TutorialPage {
  id: string;
  group: string;
  title: string;
  summary: string;
  icon: string;
  sections: TutorialSection[];
  interactive?: "particle-filter" | "constant-average";
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
              "连接 Sub2API，并把实际承载套餐的一个或多个 OpenAI 上游账号加入监控列表。",
              "添加全局 Sub2API 用户，并为其填写一份适用于全部上游账号的混池车主角色与完整周期权益比例。",
              "如需让车友查看数据，创建普通系统用户，再分别配置可访问页面、可见账号和可见参与者。",
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
          "配置服务地址与 Admin Token，读取并管理多个上游账号及各自的额度查询方式。",
        icon: "code-bracket",
        sections: [
          {
            title: "准备连接信息",
            steps: [
              "填写当前容器能够访问的 Sub2API 地址。Sub2API 在宿主机运行时，Docker Desktop 通常可使用 host.docker.internal。",
              "填写 Sub2API Admin Token。Token 只保存在服务端，不会发送到浏览器之外的第三方。",
              "点击“读取账号”，把实际承载套餐的一个或多个 OpenAI 上游账号加入监控列表。",
              "为每个监控账号选择被动或主动查询，保存后可逐个执行连接测试。",
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
              "每个监控账号独立选择查询方式。默认使用“被动查询”：只读取 Sub2API 已保存的额度快照，不会为了查询而额外调用 OpenAI 官方额度接口。快照会在 Sub2API 正常转发该账号的业务请求时更新。",
              "只有明确接受额外官方请求及其潜在风控影响时，才应把对应账号切换为主动查询。日常拼车监控建议保持被动查询。",
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
          "把全局 Sub2API 用户映射为参与者，再按上游账号额度池维护彼此独立的合同权益。",
        icon: "user-group",
        sections: [
          {
            title: "添加参与者并分配额度池",
            steps: [
              "进入参与者页面，添加车主和每一位车友。一个参与者只绑定一个全局 Sub2API 用户。",
              "从 Sub2API 用户列表选择对应用户，不要手工猜测用户 ID。",
              "进入“额度分配”页面：左键选择来源账号后可合并为混池，也可把选中的一个或多个账号拆回独立池；混池名称旁的编辑按钮可自定义名称。",
              "在每个池的参与者列填写合同百分比。每个池独立校验，份额合计不得超过 100%。",
            ],
          },
          {
            title: "池内权益比例怎么填",
            paragraphs: [
              "每个额度池填写双方约定的完整周期份额，而不是配置时的剩余份额。例如双方在某个池约定各占 50%，即使车主已经使用 10%，仍分别填写 50%。",
              "同一混池内的多个来源账号共用一份合同；不同池可以采用不同参与者和比例。系统按账号独立归属用量，再把该参与者在所有已分配池中的剩余权益合计成一个 Sub2API 全局余额建议。",
              "合并来源时，只有原合同完全一致才会继承比例；合同不同会清空新池份额，避免自动求和、平均或猜测。保存合同后，系统会按账号与 Sub2API 用户复用已有观测，并用当前池分组和份额立即重算建议。",
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
              "需要让车友自行查看时，由管理员先在“系统用户”页面创建登录账号，再点击该用户右侧的“编辑权限”。额度总览、账号状态、观测记录、粒子轨迹和额度统计等包含账号数据的页面都必须选择至少一个可见账号。",
              "页面权限决定普通用户能进入哪些业务功能；账号和参与者权限继续过滤对应页面与 API 中的数据。普通用户获得的业务页面均为只读，不能触发测算、调额、编辑、封禁或维护操作。",
              "所有普通系统用户始终可以进入“系统设置”，该权限不需要也不能由管理员配置。普通用户的设置页只显示绑定当前账号的 API Key 卡片，不展示任何全局配置。",
            ],
          },
        ],
        action: { label: "配置额度分配", to: "/allocation" },
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
              "百分比权益已经用尽但 Sub2API 余额仍大于 0 时，首页会建议把余额清零；Sub2API 原生调额接口不接受 0，因此需要跳转管理后台手动处理。",
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
              "Sub2API 0.1.179 起可以在渠道定价中直接设置 FAST 倍率。OpenAI OAuth 渠道设为 2.5 后，应保持 Sub2Pool 的兼容修正关闭，避免重复计费。",
              "兼容修正仅用于 Sub2API 仍按 2 倍记录 FAST 成本的旧版或未调整渠道。启用后，系统会只读成功采样区间内的请求日志并补足到 2.5 倍。",
              "关闭兼容修正只会停止后续完整采样的日志修正，不会删除已有事实；历史修正仍参与重放和成本拆分。缺失的历史事实保持未知，不会用当前日志推测或覆盖。",
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
    label: "算法讲解",
    pages: [
      {
        id: "particle-filter-algorithm",
        group: "算法讲解",
        title: "粒子滤波：让许多可能性一起工作",
        summary:
          "从候选答案、整数显示规则、容量路径、筛选到余额建议，一步一步看懂动态额度模型。",
        icon: "sparkles",
        sections: [],
        interactive: "particle-filter",
      },
      {
        id: "constant-average-algorithm",
        group: "算法讲解",
        title: "平均恒定：用一段历史得到一个平均值",
        summary:
          "用可拖动的端点完整走一遍简单折算，并理解它适合回答什么、不适合回答什么。",
        icon: "calculator",
        sections: [],
        interactive: "constant-average",
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
        summary: "处理官方周期刷新、福利刷新、管理员起点区间和周期中途加入。",
        icon: "arrow-path",
        sections: [
          {
            title: "周期起点怎么确定",
            paragraphs: [
              "管理员指定的起点区间优先级最高；没有管理员区间时，系统使用上游 reset_at 减去七天推导官方周期起点。",
              "设置时先选择开始记录，再选择同一条或更晚的记录作为结束。两个端点都属于同一周期，期间的连续 0%、reset_at 漂移和其他起点不会再次切分周期；结束后使用结束记录的官方窗口证据恢复自动判断。",
              "同一 reset_at 下的百分比回退会作为异常观测排除，不会仅因为连续低值就擅自建立新周期。确认发生官方赠送刷新时，可用起点区间覆盖从可靠 0% 到首次正常消费的全部观测。",
            ],
          },
          {
            title: "刷新后的估计",
            paragraphs: [
              "新增观测会从当前归属区间的起点开始重建。设置或取消人工起点区间、排除、恢复时，只从最早受影响区间向后重算，更早区间保持不变。",
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
        summary:
          "用不可变本地计划审计来源事实并确定性重放派生结果，同时安全迁移数据库。",
        icon: "wrench-screwdriver",
        sections: [
          {
            title: "本地审计与确定性重放",
            paragraphs: [
              "“本地审计并重放”会检查所有观测与非观测采样点的窗口、用户集合、账号残差和历史 FAST 明细；它从计划创建到应用都不会连接 Sub2API，也不会改写来源成本。",
              "确认应用时只提交 plan id 与 digest。源事实、配置、参与者策略、算法版本或计划内容发生变化时会拒绝应用；通过后只重新生成可派生结果。",
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
  {
    label: "API 文档",
    pages: [
      {
        id: "api",
        group: "API 文档",
        title: "API",
        summary:
          "使用管理员全权限 Key 或系统用户权限范围内的 Key 调用业务 API。",
        icon: "code-bracket",
        sections: [
          {
            title: "生成和保存 API Key",
            steps: [
              "管理员或普通系统用户进入“系统设置 → API”，点击“生成 API Key”。普通用户无需额外的“系统设置”页面授权，且只能看到自己的 API Key 卡片。",
              "完整 Key 只在生成后的模态框中显示一次，请立即复制到调用方的密钥管理系统。",
              "关闭模态框后，页面只显示尾部四位。服务端只保存 SHA-256 摘要，无法找回原 Key。",
              "Key 没有到期时间。重新生成会立即使该账号的旧 Key 失效；点击“废弃”会关闭该 Key 的外部访问。",
              "管理员维护一枚全局 API Key，拥有全部已开放 /api/v1 权限，并可一键设置建议余额。",
              "每个普通系统用户维护自己的 API Key。端点权限实时继承该用户的页面授权，响应继续受可见账号和参与者限制，且不能执行一键设置或其他管理写操作。",
            ],
            notes: [
              {
                tone: "warning",
                title: "仅通过 HTTPS 传输",
                text: "管理员 Key 等同于完整外部 API 权限，系统用户 Key 也属于敏感凭据。只应交给受信任的服务端程序；不要放在 URL、查询参数、日志或前端公开代码中。",
              },
            ],
          },
          {
            title: "认证与响应格式",
            paragraphs: [
              "每次请求都把完整 Key 放入标准 HTTP Authorization 请求头，认证方案为 Bearer。外部接口统一位于 /api/v1 下；允许的方法以各端点的 OpenAPI 定义为准。",
              '业务数据接口和 /api/v1 索引使用 { ok: true, data: ... } 响应；/api/v1/openapi.json 直接返回标准 OpenAPI 文档。失败响应使用 { ok: false, message: "..." }，字段校验失败时还可能包含 details。',
            ],
            codeBlocks: [
              {
                title: "认证请求",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1/participants \\
  --header 'Accept: application/json' \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'`,
              },
              {
                title: "常见状态码",
                language: "text",
                code: `200  请求成功
400  查询参数无效
401  未提供 Key、Key 无效、已重新生成或已废弃
403  当前系统用户没有该端点对应的页面权限，或尝试执行管理写操作
404  参与者、观测记录或授权范围内的资源不存在
405  该端点不允许所用 HTTP 方法
409  当前建议不可应用、尚未形成当前周期或上游账号状态冲突
502  读取或写入 Sub2API 数据失败`,
              },
            ],
          },
          {
            title: "从接口读取文档",
            paragraphs: [
              "GET /api/v1 返回 API 名称、版本、认证方式、端点索引和 OpenAPI 文档地址。管理员 Key 可发现全部能力；系统用户 Key 只会看到当前页面权限允许的端点。",
              "GET /api/v1/openapi.json 返回与当前 Key 权限匹配的 OpenAPI 3.1 文档，可下载后导入 Postman、Apifox、Insomnia 或代码生成工具。这两个文档端点使用同一枚 Bearer API Key，不需要先登录网页。",
            ],
            codeBlocks: [
              {
                title: "读取端点索引",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1 \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'`,
              },
              {
                title: "下载 OpenAPI 3.1 文档",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1/openapi.json \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --output sub2pool-openapi.json`,
              },
            ],
            notes: [
              {
                tone: "info",
                title: "文档也受 API Key 保护",
                text: "没有有效 API Key 时，索引和 OpenAPI 文档都会返回 401；它们不会向匿名访问者公开你的接口结构。",
              },
            ],
          },
          {
            title: "账号发现、额度总览与上游状态",
            paragraphs: [
              "GET /api/v1/accounts 只在当前 Key 拥有“账号状态”页面权限时出现，并只返回该系统用户获准查看的监控账号。其他按账号查询的接口使用这里的 id 作为 account_id；external_account_id 才是 Sub2API 上游账号 ID。",
              "GET /api/v1/dashboard 返回额度总览、当前周期、待处理建议和授权账号摘要。account_id 可选；省略时选择第一个已授权且启用的账号。",
              "GET /api/v1/account-status 会对授权范围内的监控账号执行 Sub2API 只读查询，返回运行状态、额度窗口、历史周期消耗和 30 天统计；周期消耗使用本地已持久化的粒子估计百分比与折算额度，FAST 修正总额也来自本地已持久化事实。单个账号失败会写入该账号的 warnings，不会中断其他账号。",
            ],
            codeBlocks: [
              {
                title: "发现账号并读取指定账号总览",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1/accounts \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'

curl --get \\
  --url https://sub2pool.example.com/api/v1/dashboard \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --data-urlencode 'account_id=3'`,
              },
              {
                title: "读取全部上游账号状态",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1/account-status \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'`,
              },
            ],
          },
          {
            title: "待应用建议与一键设置余额",
            paragraphs: [
              "GET /api/v1/recommendations 需要“额度总览”页面权限，返回当前 Key 可见的待应用建议。若聚合建议包含未授权账号，系统不会返回可能误导或泄露范围外数据的聚合建议。",
              "POST /api/v1/recommendations/{participant_id}/apply 仅出现在管理员全局 Key 的索引和 OpenAPI 文档中。服务端会重新读取并校验最新聚合建议，再通过幂等操作日志写入 Sub2API；普通系统用户 Key 不能调用。",
            ],
            codeBlocks: [
              {
                title: "读取并应用参与者 12 的建议",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1/recommendations \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'

curl --request POST \\
  --url https://sub2pool.example.com/api/v1/recommendations/12/apply \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'`,
              },
            ],
          },
          {
            title: "观测记录与 FAST 修正事实",
            paragraphs: [
              "GET /api/v1/observations 分页返回一个监控账号的观测记录、参与者快照和筛选后汇总。account_id 可选；省略时选择第一个启用账号。",
              "GET /api/v1/observations/{observation_id}/fast-correction 返回该原始采样区间已经持久化的 FAST 请求数、计费成本、修正金额和逐用户明细。该接口不会联网补算缺失事实。",
            ],
            bullets: [
              "page、page_size：页码和每页数量；page_size 最大 100。",
              "from、to：ISO 8601 观测时间边界。",
              "source：scheduled、manual、exhausted 或 reset。",
              "query_mode：passive 或 direct。",
            ],
            codeBlocks: [
              {
                title: "读取被动采集的最近观测",
                language: "bash",
                code: `curl --get \\
  --url https://sub2pool.example.com/api/v1/observations \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --data-urlencode 'account_id=3' \\
  --data-urlencode 'query_mode=passive' \\
  --data-urlencode 'page_size=50'`,
              },
              {
                title: "读取观测 42 的 FAST 明细",
                language: "bash",
                code: `curl --request GET \\
  --url https://sub2pool.example.com/api/v1/observations/42/fast-correction \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey'`,
              },
            ],
          },
          {
            title: "粒子轨迹与通知记录",
            paragraphs: [
              "GET /api/v1/particle-trajectory 对所选账号和周期执行确定性只读重放，返回容量区间、代表粒子、有效样本量和自适应范围扩展事件。account_id 与 period 均可选；period 使用响应 periods 数组中的 id。",
              "GET /api/v1/notifications 分页返回通知收件人、主题、正文、发送状态和错误，同时提供类型、参与者和状态筛选项。",
            ],
            bullets: [
              "通知支持 page、page_size、from、to、event_type、participant、subject 和 status。",
              'participant 可传参与者 ID；传 "system" 只读取不关联参与者的系统通知。',
              "通知正文和收件地址属于敏感业务数据，应限制调用方和日志访问范围。",
            ],
            codeBlocks: [
              {
                title: "读取指定账号的当前粒子轨迹",
                language: "bash",
                code: `curl --get \\
  --url https://sub2pool.example.com/api/v1/particle-trajectory \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --data-urlencode 'account_id=3'`,
              },
              {
                title: "读取失败通知",
                language: "bash",
                code: `curl --get \\
  --url https://sub2pool.example.com/api/v1/notifications \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --data-urlencode 'status=failed' \\
  --data-urlencode 'page_size=50'`,
              },
            ],
          },
          {
            title: "GET /api/v1/participants",
            paragraphs: [
              "返回参与者页面表格所需的全部参与者。该接口没有分页和查询参数，因为参与者通常只有少量记录。",
              "每个数组项的 id 是 Sub2Pool 参与者 ID；sub2api_user_id 是绑定的 Sub2API 用户 ID。sub2api_identity 按“用户名、邮箱、账号 ID”的顺序选择可读标识。",
            ],
            bullets: [
              "全局身份：id、name、email、notes、enabled、is_owner，以及唯一的 Sub2API 用户映射字段。",
              "pool_allocations：参与者在各额度池中的 share_percent、池名称和 account_ids；不存在覆盖全部账号的全局份额。",
              "account_breakdowns：各监控账号所属 pool_id、池内合同份额、账号成本缓存和局部测算 snapshot。",
              "最近全局余额：latest_balance_usd、last_checked_at；snapshot 按池分区汇总余额建议、完整性、调整状态和 sources 明细。",
            ],
            codeBlocks: [
              {
                title: "响应示例",
                language: "json",
                code: `{
  "ok": true,
  "data": [
    {
      "id": 1,
      "name": "车友",
      "sub2api_user_id": 22001,
      "sub2api_identity": "rider@example.com",
      "pool_allocations": [
        {
          "pool_id": 7,
          "pool_name": "默认混池",
          "share_percent": 40.0,
          "account_ids": [3]
        }
      ],
      "is_owner": false,
      "enabled": true,
      "latest_balance_usd": 320.0,
      "account_breakdowns": [
        {
          "account_id": 3,
          "external_account_id": 8801,
          "account_name": "主账号",
          "account_enabled": true,
          "pool_id": 7,
          "pool_name": "默认混池",
          "contract_share_percent": 40.0,
          "allocated": true,
          "latest_selected_cost": 480.0,
          "snapshot": {
            "charged_cycle_percent": 24.0,
            "remaining_share_percent": 16.0
          }
        }
      ],
      "snapshot": {
        "allocation_model": "partitioned_pool_sum",
        "pool_allocations": [
          {
            "pool_id": 7,
            "pool_name": "默认混池",
            "share_percent": 40.0,
            "account_ids": [3]
          }
        ],
        "recommendation_complete": true,
        "account_count": 1,
        "pool_count": 1,
        "recommended_balance_usd": 315.2,
        "sources": []
      }
    }
  ]
}`,
              },
            ],
          },
          {
            title: "GET /api/v1/statistics",
            paragraphs: [
              "按 account_id 返回一个监控账号的容量历史、当前折算摘要和参与者用量序列。account_id 使用 GET /api/v1/accounts 返回的内部 id，不是 external_account_id。",
              "capacity_series 是所选账号按天或按月的周限等效额度历史；capacity_summary 包含本周期累计折算、今日折算及其计算依据；participant_series 包含所有启用参与者在所选账号中的用量。",
            ],
            bullets: [
              "account_id：必填的 Sub2Pool 监控账号内部 ID。",
              "capacity_period：day 或 month，默认 day。",
              "capacity_days：容量历史回看天数。按天默认 90，按月默认 365，最大 730。",
              "usage_days：参与者用量回看天数，默认 7，最大 90。",
              "usage_precision：raw、hour 或 day，默认 hour。",
              "响应同时返回 fast_correction_enabled、sample_interval_minutes 和实际采用的查询参数。",
            ],
            codeBlocks: [
              {
                title: "按天读取最近 30 天容量，并按小时读取最近 7 天用量",
                language: "bash",
                code: `curl --get \\
  --url 'https://sub2pool.example.com/api/v1/statistics' \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --data-urlencode 'account_id=3' \\
  --data-urlencode 'capacity_period=day' \\
  --data-urlencode 'capacity_days=30' \\
  --data-urlencode 'usage_days=7' \\
  --data-urlencode 'usage_precision=hour'`,
              },
              {
                title: "响应骨架",
                language: "json",
                code: `{
  "ok": true,
  "data": {
    "account": {"id": 3, "external_account_id": 8801, "name": "主账号"},
    "capacity_period": "day",
    "capacity_series": [],
    "fast_correction_enabled": true,
    "capacity_summary": {
      "cycle": {},
      "today": {}
    },
    "usage_days": 7,
    "usage_precision": "hour",
    "sample_interval_minutes": 10,
    "participant_series": []
  }
}`,
              },
            ],
          },
          {
            title:
              "GET /api/v1/statistics/participants/{participant_id}/api-usage",
            paragraphs: [
              "按必填 account_id 返回一个参与者在所选上游账号当前周期内的 API Key 用量构成。路径中的 participant_id 使用参与者接口返回的 id，而不是 sub2api_user_id。",
              "结论包含统计起止时间、成本口径、FAST 修正状态、参与者在该账号中的周期用量、账号周限估计、参与者占该账号总周限比例，以及每个 API Key 的美元用量和两种百分比。",
              "服务优先返回所选账号一小时内的数据库结论。缓存过期时会向 Sub2API 执行一次带账号过滤的只读日志查询并保存新的汇总结论，不会调用 OpenAI 官方额度接口，也不会修改任何额度。",
            ],
            bullets: [
              "participant_usage_percent：该 API Key 用量占此参与者当前周期总用量的比例。",
              "weekly_quota_percent：该 API Key 用量占当前估算总周限的比例。",
              "api_keys 中仅列出当前周期存在或产生过用量的密钥；历史已删除但有用量的密钥仍会保留汇总项。",
            ],
            codeBlocks: [
              {
                title: "读取参与者 1 的 API 用量构成",
                language: "bash",
                code: `curl --get \\
  --url https://sub2pool.example.com/api/v1/statistics/participants/1/api-usage \\
  --header 'Authorization: Bearer sub2pool_你的完整APIKey' \\
  --data-urlencode 'account_id=3'`,
              },
              {
                title: "响应示例",
                language: "json",
                code: `{
  "ok": true,
  "data": {
    "participant_id": 1,
    "participant_name": "车友",
    "sub2api_user_id": 22001,
    "starts_at": "2026-08-05T00:00:00+00:00",
    "observed_to": "2026-08-11T09:20:00+00:00",
    "cost_basis": "actual",
    "fast_correction_enabled": true,
    "participant_total_usd": 480.0,
    "weekly_total_estimate_usd": 2000.0,
    "participant_weekly_percent": 24.0,
    "api_keys": [
      {
        "api_key_id": 8,
        "name": "主密钥",
        "status": "active",
        "usage_usd": 360.0,
        "participant_usage_percent": 75.0,
        "weekly_quota_percent": 18.0
      }
    ]
  }
}`,
              },
            ],
          },
          {
            title: "权限边界",
            bullets: [
              "API Key 默认拥有全部已开放 /api/v1 权限，可读取监控账号、总览、待应用建议、参与者、观测与 FAST 明细、粒子轨迹、统计、API 用量构成和通知记录。",
              "API Key 可调用 POST /api/v1/recommendations/{participant_id}/apply；服务端只应用当前有效建议，不接受调用方指定余额。",
              "系统设置、系统用户、登录记录、IP 封禁和数据库备份尚未开放为 /api/v1 操作，因此不在当前外部 API 能力范围内。",
              "网页登录 JWT 与 API Key 相互独立；重新生成或废弃 API Key 不会影响网页登录。",
            ],
          },
        ],
        action: { label: "管理 API Key", to: "/settings" },
      },
    ],
  },
];

export const tutorialPages = tutorialGroups.flatMap((group) => group.pages);
