import { NextRequest, NextResponse } from "next/server";

import { cookieOptions, decryptCookieValue, SESSION_COOKIE, type AuthSession } from "@/lib/oidc";

export const runtime = "nodejs";

const JIRA_STATE_COOKIE = "astra_jira_oauth_state";

export async function GET(request: NextRequest) {
  const session = decryptCookieValue<AuthSession>(request.cookies.get(SESSION_COOKIE)?.value);
  if (!session) {
    return NextResponse.redirect(new URL("/login?error=auth", request.url));
  }

  const stateCookie = decryptCookieValue<{ state: string; redirectUri: string; returnTo?: string }>(
    request.cookies.get(JIRA_STATE_COOKIE)?.value,
  );
  const returnTo = stateCookie?.returnTo === "/dashboard/projects" ? stateCookie.returnTo : "/dashboard";
  const redirectWithStatus = (status: "connected" | "error", value: string) => {
    const destination = new URL(returnTo, request.url);
    destination.searchParams.set(status === "connected" ? "jira_status" : "jira_error", value);
    return NextResponse.redirect(destination);
  };
  const callbackState = request.nextUrl.searchParams.get("state");
  const code = request.nextUrl.searchParams.get("code");
  const error = request.nextUrl.searchParams.get("error");

  if (!stateCookie || !callbackState || stateCookie.state !== callbackState) {
    const response = redirectWithStatus("error", "state");
    response.cookies.set(JIRA_STATE_COOKIE, "", cookieOptions(0));
    return response;
  }

  if (error) {
    const response = redirectWithStatus("error", "denied");
    response.cookies.set(JIRA_STATE_COOKIE, "", cookieOptions(0));
    return response;
  }

  if (!code) {
    const response = redirectWithStatus("error", "missing_code");
    response.cookies.set(JIRA_STATE_COOKIE, "", cookieOptions(0));
    return response;
  }

  try {
    const backendOrigin = process.env.ASTRA_BACKEND_URL ?? "http://localhost:8003";
    const backendUrl = new URL("/api/v1/integrations/jira/oauth/callback", backendOrigin.replace(/\/$/, "") + "/");
    const upstream = await fetch(backendUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${session.accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        code,
        state: callbackState,
        redirect_uri: stateCookie.redirectUri,
      }),
      cache: "no-store",
    });

    const response = redirectWithStatus("connected", "connected");
    response.cookies.set(JIRA_STATE_COOKIE, "", cookieOptions(0));

    if (!upstream.ok) {
      const errorResponse = redirectWithStatus("error", `callback-${upstream.status}`);
      errorResponse.cookies.set(JIRA_STATE_COOKIE, "", cookieOptions(0));
      return errorResponse;
    }

    return response;
  } catch {
    const response = redirectWithStatus("error", "unavailable");
    response.cookies.set(JIRA_STATE_COOKIE, "", cookieOptions(0));
    return response;
  }
}
