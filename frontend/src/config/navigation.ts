export interface NavigationLink {
  label: string;
  to: string;
}

export interface NavigationGroup {
  label: string;
  icon: string;
  to?: string;
  adminOnly?: boolean;
  children?: NavigationLink[];
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
    adminOnly: true,
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
    label: "使用教程",
    icon: "book-open",
    to: "/tutorial",
    adminOnly: true,
  },
  {
    label: "系统设置",
    icon: "cog-6-tooth",
    to: "/settings",
    adminOnly: true,
  },
];
