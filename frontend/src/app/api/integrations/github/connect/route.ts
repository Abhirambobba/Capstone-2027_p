import { NextRequest, NextResponse } from "next/server";

import { cookieOptions, decryptCookieValue, encryptCookieValue, refreshSession, SESSION_COOKIE, type AuthSession } from "@/lib/oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const current = decryptCookieValue<AuthSession>(request.cookies.get(SESSION_COOKIE)?.value);
  if (!current) return NextResponse.redirect(new URL("/login?error=auth", request.url));

  const returnTo = request.nextUrl.searchParams.get("returnTo") ?? "/dashboard/projects";
  const safeReturnTo = /^\/dashboard\/projects(?:\/[A-Za-z0-9_-]+)?$/.test(returnTo)
    ? returnTo
    : "/dashboard/projects";

  try {
    const session = await refreshSession(current);
    const backendOrigin = process.env.ASTRA_BACKEND_URL ?? "http://localhost:8003";
    const backendUrl = new URL("/api/v1/integrations/github/connect", `${backendOrigin.replace(/\/$/, "")}/`);
    backendUrl.searchParams.set("return_to", safeReturnTo);
    const upstream = await fetch(backendUrl, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
      cache: "no-store",
    });
    if (!upstream.ok) {
      const destination = new URL(safeReturnTo, request.url);
      destination.searchParams.set("github_error", "connect");
      return NextResponse.redirect(destination);
    }

    const payload = await upstream.json() as { connected?: boolean; return_to?: string };
    if (!payload.connected) throw new Error("GitHub connection was not verified");
    const destination = new URL(payload.return_to ?? safeReturnTo, request.url);
    destination.searchParams.set("github_status", "connected");
    const response = NextResponse.redirect(destination);
    if (session.accessToken !== current.accessToken) {
      response.cookies.set(SESSION_COOKIE, encryptCookieValue(session), cookieOptions(60 * 60 * 24 * 30));
    }
    return response;
  } catch {
    const destination = new URL(safeReturnTo, request.url);
    destination.searchParams.set("github_error", "unavailable");
    return NextResponse.redirect(destination);
  }
}