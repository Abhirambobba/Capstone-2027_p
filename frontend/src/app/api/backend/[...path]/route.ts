import { NextRequest, NextResponse } from "next/server";
import { cookieOptions, decryptCookieValue, encryptCookieValue, refreshSession, SESSION_COOKIE, type AuthSession } from "@/lib/oidc";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const current = decryptCookieValue<AuthSession>(request.cookies.get(SESSION_COOKIE)?.value);
  if (!current) return NextResponse.json({ detail: "Authentication required." }, { status: 401 });

  let session: AuthSession;
  try {
    session = await refreshSession(current);
  } catch {
    const response = NextResponse.json({ detail: "Cognito session expired." }, { status: 401 });
    response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
    return response;
  }

  try {
    const { path } = await context.params;
    const backendOrigin = process.env.ASTRA_BACKEND_URL ?? "http://localhost:8003";
    const backendUrl = new URL(`${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, `${backendOrigin.replace(/\/$/, "")}/`);
    const headers = new Headers();
    for (const name of ["accept", "content-type"]) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    headers.set("Authorization", `Bearer ${session.accessToken}`);
    const method = request.method;
    const body = ["GET", "HEAD"].includes(method) ? undefined : await request.arrayBuffer();
    const upstream = await fetch(backendUrl, { method, headers, body, cache: "no-store" });
    const responseHeaders = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType) responseHeaders.set("content-type", contentType);
    const response = new NextResponse(upstream.body, { status: upstream.status, headers: responseHeaders });
    if (upstream.status === 401) response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
    if (session.accessToken !== current.accessToken) {
      response.cookies.set(SESSION_COOKIE, encryptCookieValue(session), cookieOptions(60 * 60 * 24 * 30));
    }
    return response;
  } catch {
    return NextResponse.json({ detail: "Astra API is unavailable." }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;