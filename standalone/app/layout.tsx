import type { Metadata } from "next";
import { ThemeProvider } from "./components/ThemeProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Compastral · AI use cases for any company",
  description:
    "Compastral (company × Mistral) — a Mistral Workflows pipeline that produces three relevant, iconic, high-impact GenAI use cases for any company.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
