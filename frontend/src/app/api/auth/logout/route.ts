import { NextRequest, NextResponse } from "next/server";
import { cognitoClientId, cognitoHostedDomain, cookieOptions, SESSION_COOKIE, TRANSACTION_COOKIE } from "@/lib/oidc";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const logoutUri = new URL("/login", request.url).toString();
  const logoutUrl = new URL("/logout", cognitoHostedDomain());
  logoutUrl.searchParams.set("client_id", cognitoClientId());
  logoutUrl.searchParams.set("logout_uri", logoutUri);
  const response = NextResponse.redirect(logoutUrl);
  response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
  response.cookies.set(TRANSACTION_COOKIE, "", cookieOptions(0));
  return response;
}