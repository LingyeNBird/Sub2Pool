/// <reference types="vite/client" />

declare module "@demo-backend" {
  export function demoRequest(
    path: string,
    options?: RequestInit,
  ): Promise<Response>;
}

interface ImportMetaEnv {
  readonly VITE_DEMO_MODE?: string;
  readonly VITE_PUBLIC_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
