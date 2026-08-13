import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

const demoBackend = fileURLToPath(
  new URL("./src/demo/backend.ts", import.meta.url),
);
const disabledDemoBackend = fileURLToPath(
  new URL("./src/demo/disabled.ts", import.meta.url),
);

export default defineConfig(({ command, mode }) => ({
  // Django 构建继续使用 /static/frontend/；GitHub Pages 使用工作流注入的项目路径。
  base:
    mode === "demo"
      ? process.env.VITE_PUBLIC_BASE || "/Sub2Pool/"
      : command === "build"
        ? "/static/frontend/"
        : "/",
  plugins: [
    tailwindcss(),
    vue({
      template: {
        compilerOptions: {
          isCustomElement: (tag) => tag.startsWith("tc-"),
        },
      },
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@demo-backend": mode === "demo" ? demoBackend : disabledDemoBackend,
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
      "/admin": process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
    },
  },
}));
