import "vue-router";
import type { PagePermission } from "@/config/pagePermissions";

export {};

declare module "vue-router" {
  interface RouteMeta {
    title: string;
    public?: boolean;
    permission?: PagePermission;
  }
}
