import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Griot — Sonic Bridge",
  description: "Turn any set of songs into a seamless album, built from full-song analysis.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
