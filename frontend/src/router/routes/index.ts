import type { RouteRecordRaw } from "vue-router";
import type { PagePermission } from "@/config/pagePermissions";

const pageMeta = (title: string, permission: PagePermission) => ({
  title,
  permission,
});

export const appRoutes: RouteRecordRaw[] = [
  {
    path: "",
    name: "dashboard",
    component: () => import("@/views/index/Index.vue"),
    meta: pageMeta("额度总览", "dashboard"),
  },
  {
    path: "account-status",
    name: "account-status",
    component: () => import("@/views/account-status/AccountStatusView.vue"),
    meta: pageMeta("账号状态", "account_status"),
  },
  {
    path: "participants",
    name: "participants",
    component: () => import("@/views/users/participants/ParticipantsView.vue"),
    meta: pageMeta("参与者", "participants"),
  },
  {
    path: "allocation",
    name: "allocation",
    component: () => import("@/views/users/allocation/AllocationView.vue"),
    meta: pageMeta("额度分配", "participants"),
  },
  {
    path: "system-users",
    name: "system-users",
    component: () => import("@/views/users/system-users/SystemUsersView.vue"),
    meta: pageMeta("系统用户", "system_users"),
  },
  {
    path: "observations",
    name: "observations",
    component: () => import("@/views/tools/audit-logs/AuditLogs.vue"),
    meta: pageMeta("观测记录", "observations"),
  },
  {
    path: "particle-filter",
    name: "particle-filter",
    component: () => import("@/views/particle-filter/ParticleFilterView.vue"),
    meta: pageMeta("粒子轨迹", "particle_filter"),
  },
  {
    path: "statistics",
    name: "statistics",
    component: () => import("@/views/statistics/StatisticsView.vue"),
    meta: pageMeta("额度统计", "statistics"),
  },
  {
    path: "notifications",
    name: "notifications",
    component: () => import("@/views/transactions/logs/Logs.vue"),
    meta: pageMeta("通知记录", "notifications"),
  },
  {
    path: "login-records",
    name: "login-records",
    component: () => import("@/views/login-records/LoginRecordsView.vue"),
    meta: pageMeta("登录记录", "login_records"),
  },
  {
    path: "tutorial",
    name: "tutorial",
    component: () => import("@/views/TutorialView.vue"),
    meta: pageMeta("使用教程", "tutorial"),
  },
  {
    path: "settings",
    name: "settings",
    component: () => import("@/views/settings/general/General.vue"),
    meta: pageMeta("系统设置", "settings"),
  },
];
