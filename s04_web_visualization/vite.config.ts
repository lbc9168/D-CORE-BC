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
  // 使用相对路径，使 dist/ 可在任意位置通过本地服务器访问
  base: "./",
});
