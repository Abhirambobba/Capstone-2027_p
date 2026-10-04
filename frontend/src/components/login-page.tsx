"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-provider";

function destination(accessRole: "MANAGER" | "EMPLOYEE", employeeId: string) {
  return accessRole === "MANAGER" ? "/dashboard" : `/dashboard/workforce/employees/${employeeId}`;
}

export function LoginPage() {
  const { user, status, login } = useAuth();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (user) router.replace(destination(user.access_role, user.employee_id));
  }, [user, router]);

  async function startLogin() {
    setError(null);
    try {
      await login();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Cognito sign-in could not be started.");
    }
  }

  return <main className="login-page"><section className="login-panel"><div className="brand-mark">A</div><p className="eyebrow">Astra workspace</p><h1>Sign in</h1><p>Continue through your organization’s Cognito sign-in.</p>{status === "unconfigured" && <p className="field-error">Cognito is not configured for this frontend. Set the pool, client, region, and hosted UI domain.</p>}{error && <p className="field-error" role="alert">{error}</p>}<button className="button primary" type="button" onClick={() => void startLogin()} disabled={status === "loading" || status === "unconfigured"}>{status === "loading" ? "Checking session…" : "Continue with Cognito"}</button></section></main>;
}

export function AuthCallbackPage() {
  const { user, status } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (user) router.replace(destination(user.access_role, user.employee_id));
  }, [user, router]);
  return <main className="auth-loading" aria-live="polite">{status === "unauthenticated" ? "Completing Cognito sign-in…" : "Verifying your Astra account…"}</main>;
}