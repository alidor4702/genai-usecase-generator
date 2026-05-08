import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        mistral: {
          orange: "#FA552E",
          dark: "#0E1116",
          surface: "#161B22",
          border: "#30363D",
        },
      },
    },
  },
  plugins: [],
};
export default config;
