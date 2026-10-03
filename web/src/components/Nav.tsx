"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

const LINKS = [
  { href: "/", label: "New ticket" },
  { href: "/runs", label: "Runs" },
  { href: "/approvals", label: "Approvals" },
];

export function Nav() {
  const pathname = usePathname();
  const pending = useQuery({ queryKey: ["approvals"], queryFn: api.pendingApprovals, refetchInterval: 5_000 });
  const pendingCount = pending.data?.length ?? 0;

  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 sm:px-6">
        <Link href="/" className="font-semibold text-slate-900">
          FieldOps <span className="text-sky-600">Agent</span>
        </Link>
        <nav className="flex gap-1 text-sm">
          {LINKS.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-1.5 ${active ? "bg-slate-100 font-medium" : "text-slate-600 hover:bg-slate-50"}`}
              >
                {link.label}
                {link.href === "/approvals" && pendingCount > 0 && (
                  <span className="ml-1.5 rounded-full bg-amber-500 px-1.5 text-xs font-semibold text-white">
                    {pendingCount}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <span className="ml-auto hidden text-xs text-slate-500 sm:block">CoolTech Services · Colombo time</span>
      </div>
    </header>
  );
}
