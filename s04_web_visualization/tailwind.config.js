/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Noto Sans SC", "system-ui", "sans-serif"],
      },
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
        // 主色调（深青绿 #2F6562）
        brand: {
          50:  "#F2F8F8",
          100: "#E0EEEE",
          200: "#C2DDDC",
          300: "#96BFBD",
          400: "#639896",
          500: "#2F6562",
          600: "#285957",
          700: "#1F4544",
          800: "#143231",
          900: "#0C1F1E",
        },
      },
    },
  },
  plugins: [],
};
