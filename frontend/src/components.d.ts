import type AppIcon from "@/components/common/AppIcon.vue";

export {};

declare module "vue" {
  interface GlobalComponents {
    AppIcon: typeof AppIcon;
  }
}
