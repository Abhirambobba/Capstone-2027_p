import { NextRequest, NextResponse } from "next/server";
import { callbackUri, cookieOptions, decryptCookieValue, encryptCookieValue, getOidcClient, sessionFromTokenSet, SESSION_COOKIE, TRANSACTION_COOKIE, type OidcTransaction } from "@/lib/oidc";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  const transaction = decryptCookieValue<OidcTransaction>(request.cookies.get(TRANSACTION_COOKIE)?.value);
  if (!transaction) return NextResponse.redirect(new URL("/login?error=state", request.url));

  try {
    const redirectUri = callbackUri(request.url);
    const client = await getOidcClient(redirectUri);
    const tokenSet = await client.callback(
      redirectUri,
      client.callbackParams(request.url),
      { state: transaction.state, nonce: transaction.nonce, code_verifier: transaction.codeVerifier },
    );
    const session = sessionFromTokenSet(tokenSet);
    const response = NextResponse.redirect(new URL("/dashboard", request.url));
    response.cookies.set(SESSION_COOKIE, encryptCookieValue(session), cookieOptions(60 * 60 * 24 * 30));
    response.cookies.set(TRANSACTION_COOKIE, "", cookieOptions(0));
    return response;
  } catch {
    const response = NextResponse.redirect(new URL("/login?error=callback", request.url));
    response.cookies.set(TRANSACTION_COOKIE, "", cookieOptions(0));
    response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
    return response;
  }
}