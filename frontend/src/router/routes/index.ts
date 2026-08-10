import type { RouteRecordRaw } from "vue-router";

export const appRoutes: RouteRecordRaw[] = [
  {
    path: "",
    name: "dashboard",
    component: () => import("@/views/index/Index.vue"),
    meta: { title: "额度总览", adminOnly: true },
  },
  {
    path: "participants",
    name: "participants",
    component: () => import("@/views/users/participants/ParticipantsView.vue"),
    meta: { title: "参与者" },
  },
  {
    path: "system-users",
    name: "system-users",
    component: () => import("@/views/users/system-users/SystemUsersView.vue"),
    meta: { title: "系统用户", adminOnly: true },
  },
  {
    path: "observations",
    name: "observations",
    component: () => import("@/views/tools/audit-logs/AuditLogs.vue"),
    meta: { title: "观测记录", adminOnly: true },
  },
  {
    path: "particle-filter",
    name: "particle-filter",
    component: () => import("@/views/particle-filter/ParticleFilterView.vue"),
    meta: { title: "粒子轨迹" },
  },
  {
    path: "statistics",
    name: "statistics",
    component: () => import("@/views/statistics/StatisticsView.vue"),
    meta: { title: "额度统计" },
  },
  {
    path: "notifications",
    name: "notifications",
    component: () => import("@/views/transactions/logs/Logs.vue"),
    meta: { title: "通知记录", adminOnly: true },
  },
  {
    path: "login-records",
    name: "login-records",
    component: () => import("@/views/login-records/LoginRecordsView.vue"),
    meta: { title: "登录记录", adminOnly: true },
  },
  {
    path: "tutorial",
    name: "tutorial",
    component: () => import("@/views/TutorialView.vue"),
    meta: { title: "使用教程", adminOnly: true },
  },
  {
    path: "settings",
    name: "settings",
    component: () => import("@/views/settings/general/General.vue"),
    meta: { title: "系统设置", adminOnly: true },
  },
];
