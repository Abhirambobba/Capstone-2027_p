"use client";

import { useEffect, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-provider";

export function AuthGuard({ children }: { children: ReactNode }) {
  const { user, status } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const employeePath = user ? `/dashboard/workforce/employees/${user.employee_id}` : "";
  const employeeRouteAllowed = user?.access_role !== "EMPLOYEE" || pathname === employeePath;

  useEffect(() => {
    if (status === "unauthenticated" || status === "unconfigured") {
      router.replace("/login");
    } else if (status === "authenticated" && user?.access_role === "EMPLOYEE" && pathname !== employeePath) {
      router.replace(employeePath);
    }
  }, [status, user, pathname, employeePath, router]);

  if (status !== "authenticated" || !user || !employeeRouteAllowed) {
    return <main className="auth-loading" aria-live="polite">Checking your Astra session…</main>;
  }
  return children;
}