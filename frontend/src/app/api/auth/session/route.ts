import { NextRequest, NextResponse } from "next/server";
import { assertOidcConfiguration, cookieOptions, decryptCookieValue, encryptCookieValue, refreshSession, SESSION_COOKIE, type AuthSession } from "@/lib/oidc";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  try {
    assertOidcConfiguration();
  } catch {
    return NextResponse.json({ detail: "Cognito authentication is not configured." }, { status: 503 });
  }
  const current = decryptCookieValue<AuthSession>(request.cookies.get(SESSION_COOKIE)?.value);
  if (!current) return NextResponse.json({ detail: "Authentication required." }, { status: 401 });
  try {
    const session = await refreshSession(current);
    const response = NextResponse.json({ authenticated: true });
    if (session.accessToken !== current.accessToken) {
      response.cookies.set(SESSION_COOKIE, encryptCookieValue(session), cookieOptions(60 * 60 * 24 * 30));
    }
    return response;
  } catch {
    const response = NextResponse.json({ detail: "Cognito session expired." }, { status: 401 });
    response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
    return response;
  }
}