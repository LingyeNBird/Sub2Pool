import "vue-router";

export {};

declare module "vue-router" {
  interface RouteMeta {
    title: string;
    public?: boolean;
  }
}
