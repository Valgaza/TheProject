import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import "@/styles/tokens.css";
import "@/styles/app.css";
import "@/styles/landing.css";
import "@/styles/pages.css";
import "@/styles/graph.css";

export const metadata: Metadata = {
  title: "Nexus — local-first AI orchestration",
  description: "Nexus — local-first AI orchestration",
};

export const viewport = {
  width: "1280",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..700;1,9..144,300..700&family=Inter+Tight:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div id="root">
          <AppShell>
            {children}
          </AppShell>
        </div>
      </body>
    </html>
  );
}
