import {
  createRouter,
  createWebHashHistory,
  createWebHistory,
} from "vue-router";

import DashboardLayout from "@/layouts/DashboardLayout.vue";
import { useAuthStore } from "@/stores/auth";
import LoginView from "@/views/LoginView.vue";
import NotFoundView from "@/views/NotFoundView.vue";

import { appRoutes } from "./routes";

const router = createRouter({
  // Pages 只让服务器处理项目根路径，hash 后的业务深链由浏览器解释。
  history:
    import.meta.env.VITE_DEMO_MODE === "true"
      ? createWebHashHistory(import.meta.env.BASE_URL)
      : createWebHistory("/"),
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
