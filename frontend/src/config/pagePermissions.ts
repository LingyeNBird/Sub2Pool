export const pagePermissionCodes = [
  "dashboard",
  "account_status",
  "participants",
  "system_users",
  "observations",
  "particle_filter",
  "statistics",
  "notifications",
  "login_records",
  "settings",
  "tutorial",
] as const;

export type PagePermission = (typeof pagePermissionCodes)[number];

export interface PagePermissionOption {
  code: PagePermission;
  label: string;
  description: string;
}

export interface PagePermissionGroup {
  label: string;
  items: PagePermissionOption[];
}

export const pagePermissionGroups: PagePermissionGroup[] = [
  {
    label: "额度与监控",
    items: [
      {
        code: "dashboard",
        label: "额度总览",
        description: "查看额度建议和周期摘要",
      },
      {
        code: "account_status",
        label: "账号状态",
        description: "查看上游账号运行和额度状态",
      },
      {
        code: "observations",
        label: "观测记录",
        description: "查看采样记录及只读明细",
      },
      {
        code: "particle_filter",
        label: "粒子轨迹",
        description: "查看容量模型和历史轨迹",
      },
      {
        code: "statistics",
        label: "额度统计",
        description: "查看容量和参与者用量统计",
      },
    ],
  },
  {
    label: "用户与系统",
    items: [
      {
        code: "participants",
        label: "参与者",
        description: "查看授权范围内的参与者",
      },
      {
        code: "system_users",
        label: "系统用户",
        description: "只读查看普通系统用户",
      },
      {
        code: "notifications",
        label: "通知记录",
        description: "查看授权参与者和系统通知",
      },
      {
        code: "login_records",
        label: "登录记录",
        description: "只读查看登录和封禁记录",
      },
      {
        code: "settings",
        label: "系统设置",
        description: "只读查看脱敏后的系统配置",
      },
      {
        code: "tutorial",
        label: "使用教程",
        description: "查看内置使用和算法教程",
      },
    ],
  },
];

export const participantScopedPagePermissions = new Set<PagePermission>([
  "dashboard",
  "participants",
  "observations",
  "statistics",
  "notifications",
]);

export const pagePermissionRoutes: Array<{
  code: PagePermission;
  path: string;
}> = [
  { code: "dashboard", path: "/" },
  { code: "account_status", path: "/account-status" },
  { code: "participants", path: "/participants" },
  { code: "system_users", path: "/system-users" },
  { code: "observations", path: "/observations" },
  { code: "particle_filter", path: "/particle-filter" },
  { code: "statistics", path: "/statistics" },
  { code: "notifications", path: "/notifications" },
  { code: "login_records", path: "/login-records" },
  { code: "settings", path: "/settings" },
  { code: "tutorial", path: "/tutorial" },
];
