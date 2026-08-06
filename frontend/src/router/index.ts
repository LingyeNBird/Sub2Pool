import { createRouter, createWebHistory } from "vue-router";

import DashboardLayout from "@/layouts/DashboardLayout.vue";
import { useAuthStore } from "@/stores/auth";
import LoginView from "@/views/LoginView.vue";
import NotFoundView from "@/views/NotFoundView.vue";

import { appRoutes } from "./routes";

const router = createRouter({
  // 资源位于 /static/frontend/，但业务路由始终从站点根路径开始。
  history: createWebHistory("/"),
  routes: [
    {
      path: "/login",
      name: "login",
      component: LoginView,
      meta: { title: "登录", public: true },
    },
    {
      path: "/",
      component: DashboardLayout,
      children: [
        ...appRoutes,
        {
          path: ":pathMatch(.*)*",
          name: "not-found",
          component: NotFoundView,
          meta: { title: "页面不存在", adminOnly: true },
        },
      ],
    },
  ],
  scrollBehavior: () => ({ left: 0, top: 0 }),
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  const authenticated = await auth.refresh();
  if (!to.meta.public && !authenticated) {
    return { name: "login", query: { next: to.fullPath } };
  }
  if (to.name === "login" && authenticated) {
    return { name: auth.isStaff ? "dashboard" : "statistics" };
  }
  if (authenticated && !auth.isStaff && to.meta.adminOnly) {
    return { name: "statistics" };
  }
  return true;
});

router.afterEach((route) => {
  document.title = `${String(route.meta.title ?? "额度总览")} · Sub2API 拼车额度`;
});

export default router;
