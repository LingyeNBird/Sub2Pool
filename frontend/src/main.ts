import "@/assets/styles/tailwind.css";

import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "@/App.vue";
import AppIcon from "@/components/common/AppIcon.vue";
import router from "@/router";
import { useAuthStore } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";

const app = createApp(App);
const pinia = createPinia();

app.component("AppIcon", AppIcon);
app.use(pinia);

const auth = useAuthStore(pinia);
window.addEventListener("pinche:auth-expired", () => {
  auth.expire();
  if (router.currentRoute.value.name !== "login") {
    void router.replace({
      name: "login",
      query: { next: router.currentRoute.value.fullPath },
    });
  }
});

app.use(router);

useThemeStore(pinia).initialize();
app.mount("#app");
