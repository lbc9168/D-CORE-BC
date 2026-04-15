import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // 静态数据文件从 public/ 提供
  publicDir: "public",
  // 使用相对 base，兼容本地静态部署与子路径部署
  base: "./",
});
