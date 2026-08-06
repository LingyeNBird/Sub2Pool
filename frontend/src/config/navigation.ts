export interface NavigationLink {
  label: string;
  to: string;
}

export interface NavigationGroup {
  label: string;
  icon: string;
  to?: string;
  children?: NavigationLink[];
}

export const navigation: NavigationGroup[] = [
  {
    label: "额度总览",
    icon: "home",
    to: "/",
  },
  {
    label: "参与者",
    icon: "user-group",
    to: "/participants",
  },
  {
    label: "观测记录",
    icon: "chart-bar",
    to: "/observations",
  },
  {
    label: "通知记录",
    icon: "bell",
    to: "/notifications",
  },
  {
    label: "系统设置",
    icon: "cog-6-tooth",
    to: "/settings",
  },
];
