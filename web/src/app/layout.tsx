import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "FieldOps Agent",
  description: "CoolTech field service agent: tickets, live runs, approvals and KPIs",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-slate-50 text-slate-900">
        <Providers>
          <Nav />
          <main className="mx-auto max-w-6xl px-4 py-6 sm:px-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
