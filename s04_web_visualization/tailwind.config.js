/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // 能源类别色板
        category: {
          PV: "#F59E0B",  // 光伏 - amber
          WD: "#3B82F6",  // 风电 - blue
          HY: "#06B6D4",  // 水电 - cyan
          GT: "#EF4444",  // 地热 - red
          BM: "#22C55E",  // 生物质 - green
          ES: "#8B5CF6",  // 储能 - purple
          HB: "#EC4899",  // 综合 - pink
        },
        // 主色调（深青蓝）
        brand: {
          50:  "#f0f9ff",
          100: "#e0f2fe",
          200: "#bae6fd",
          300: "#7dd3fc",
          400: "#38bdf8",
          500: "#0ea5e9",
          600: "#0284c7",
          700: "#0369a1",
          800: "#075985",
          900: "#0c4a6e",
        },
      },
    },
  },
  plugins: [],
};
