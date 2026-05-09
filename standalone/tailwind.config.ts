import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        mistral: {
          orange: "#FA552E",
          orangeBright: "#FF7D5C",
          orangeSoft: "#FFB199",
          dark: "#0A0E14",
          surface: "#161B22",
          surfaceLight: "#1F242C",
          border: "#30363D",
          borderLight: "#3E444E",
        },
        ink: {
          primary: "#E6EDF3",
          secondary: "#9CA3AF",
          muted: "#6B7280",
        },
        ok: "#10B981",
        warn: "#F59E0B",
        bad: "#EF4444",
      },
      animation: {
        "drift-1": "drift1 28s ease-in-out infinite",
        "drift-2": "drift2 36s ease-in-out infinite",
        "drift-3": "drift3 22s ease-in-out infinite",
        "pulse-soft": "pulseSoft 2.4s ease-in-out infinite",
        "shimmer": "shimmer 2s linear infinite",
        "slide-in": "slideIn 0.35s cubic-bezier(0.16, 1, 0.3, 1)",
      },
      keyframes: {
        drift1: {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(20vw, 12vh) scale(1.15)" },
        },
        drift2: {
          "0%, 100%": { transform: "translate(0, 0) scale(1.1)" },
          "50%": { transform: "translate(-15vw, 18vh) scale(1)" },
        },
        drift3: {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "50%": { transform: "translate(8vw, -10vh) scale(0.9)" },
        },
        pulseSoft: {
          "0%, 100%": { opacity: "0.6" },
          "50%": { opacity: "1" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        slideIn: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
