import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GenAI Use Case Generator",
  description:
    "Mistral Workflows pipeline that produces three relevant, iconic, high-impact GenAI use cases for any company.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
