"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Employee } from "./types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "unconfigured";
type AuthContextValue = { user: Employee | null; status: AuthStatus; login: () => Promise<void>; logout: () => Promise<void> };
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Employee | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const router = useRouter();

  useEffect(() => {
    let active = true;
    const loadSession = async () => {
      try {
        const response = await fetch("/api/auth/session", { cache: "no-store", credentials: "same-origin" });
        if (response.status === 401) {
          if (active) { setUser(null); setStatus("unauthenticated"); }
          return;
        }
        if (response.status === 503) {
          if (active) { setUser(null); setStatus("unconfigured"); }
          return;
        }
        if (!response.ok) throw new Error("Session could not be checked");
        const profile = await api.me();
        if (active) { setUser(profile); setStatus("authenticated"); }
      } catch {
        if (active) { setUser(null); setStatus("unauthenticated"); }
      }
    };
    const handleApiAuthError = (event: Event) => {
      const responseStatus = (event as CustomEvent<number>).detail;
      if (responseStatus === 401) {
        setUser(null);
        setStatus("unauthenticated");
        router.replace("/login");
      } else if (responseStatus === 403) {
        router.replace("/unauthorized");
      }
    };
    window.addEventListener("astra:auth-error", handleApiAuthError);
    void loadSession();
    return () => { active = false; window.removeEventListener("astra:auth-error", handleApiAuthError); };
  }, [router]);

  async function login() {
    window.location.href = new URL("/api/auth/login", window.location.origin).toString();
  }

  async function logout() {
    window.location.href = new URL("/api/auth/logout", window.location.origin).toString();
  }

  return <AuthContext.Provider value={{ user, status, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}