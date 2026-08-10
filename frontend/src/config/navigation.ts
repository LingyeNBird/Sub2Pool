export interface NavigationLink {
  label: string;
  to: string;
  exactQuery?: boolean;
}
export interface NavigationSection {
  label: string;
  section: true;
}

export type NavigationChild = NavigationLink | NavigationSection;

export interface NavigationGroup {
  label: string;
  icon: string;
  to?: string;
  adminOnly?: boolean;
  children?: NavigationChild[];
}

export const navigation: NavigationGroup[] = [
  {
    label: "额度总览",
    icon: "home",
    to: "/",
    adminOnly: true,
  },
  {
    label: "参与者",
    icon: "user-group",
    to: "/participants",
  },
  {
    label: "系统用户",
    icon: "user-plus",
    to: "/system-users",
    adminOnly: true,
  },
  {
    label: "观测记录",
    icon: "chart-bar",
    to: "/observations",
    adminOnly: true,
  },
  {
    label: "粒子轨迹",
    icon: "sparkles",
    to: "/particle-filter",
  },
  {
    label: "额度统计",
    icon: "presentation-chart-line",
    to: "/statistics",
  },
  {
    label: "通知记录",
    icon: "bell",
    to: "/notifications",
    adminOnly: true,
  },
  {
    label: "登录记录",
    icon: "finger-print",
    to: "/login-records",
    adminOnly: true,
  },
  {
    label: "系统设置",
    icon: "cog-6-tooth",
    to: "/settings",
    adminOnly: true,
  },
  {
    label: "使用教程",
    icon: "book-open",
    adminOnly: true,
    children: [
      { label: "开始使用", section: true },
      { label: "产品概览", to: "/tutorial", exactQuery: true },
      {
        label: "连接 Sub2API",
        to: "/tutorial?page=connection",
        exactQuery: true,
      },
      {
        label: "参与者与系统用户",
        to: "/tutorial?page=participants",
        exactQuery: true,
      },
      {
        label: "首次测算",
        to: "/tutorial?page=first-measurement",
        exactQuery: true,
      },
      { label: "日常使用", section: true },
      {
        label: "额度建议与调整",
        to: "/tutorial?page=recommendations",
        exactQuery: true,
      },
      {
        label: "采样与校准",
        to: "/tutorial?page=collection",
        exactQuery: true,
      },
      {
        label: "统计、模型与粒子轨迹",
        to: "/tutorial?page=statistics",
        exactQuery: true,
      },
      { label: "算法讲解", section: true },
      {
        label: "粒子滤波算法",
        to: "/tutorial?page=particle-filter-algorithm",
        exactQuery: true,
      },
      {
        label: "平均恒定算法",
        to: "/tutorial?page=constant-average-algorithm",
        exactQuery: true,
      },
      { label: "机制与维护", section: true },
      {
        label: "周限刷新与中途拼车",
        to: "/tutorial?page=cycles",
        exactQuery: true,
      },
      {
        label: "通知与登录安全",
        to: "/tutorial?page=notifications-security",
        exactQuery: true,
      },
      {
        label: "数据维护与排错",
        to: "/tutorial?page=maintenance",
        exactQuery: true,
      },
    ],
  },
];
