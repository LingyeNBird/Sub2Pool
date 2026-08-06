import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig(({ command }) => ({
  // 生产资源由 Django 的 /static/frontend/ 提供；开发服务器仍保持根路径。
  base: command === "build" ? "/static/frontend/" : "/",
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
