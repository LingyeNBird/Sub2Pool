import type { RouteRecordRaw } from "vue-router";

export const appRoutes: RouteRecordRaw[] = [
  {
    path: "",
    name: "dashboard",
    component: () => import("@/views/index/Index.vue"),
    meta: { title: "额度总览" },
  },
  {
    path: "participants",
    name: "participants",
    component: () => import("@/views/users/Users.vue"),
    meta: { title: "参与者" },
  },
  {
    path: "observations",
    name: "observations",
    component: () => import("@/views/tools/audit-logs/AuditLogs.vue"),
    meta: { title: "观测记录" },
  },
  {
    path: "notifications",
    name: "notifications",
    component: () => import("@/views/transactions/logs/Logs.vue"),
    meta: { title: "通知记录" },
  },
  {
    path: "settings",
    name: "settings",
    component: () => import("@/views/settings/general/General.vue"),
    meta: { title: "系统设置" },
  },
];
