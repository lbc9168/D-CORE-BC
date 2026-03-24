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
  // GitHub Pages 部署到仓库子路径时，静态资源需显式带仓库前缀
  base: "/BRI_RE_Database_v2/",
});
