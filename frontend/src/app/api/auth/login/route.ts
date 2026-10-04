import { NextRequest, NextResponse } from "next/server";
import { authorizationUrl, callbackUri, cookieOptions, encryptCookieValue, getOidcClient, assertOidcConfiguration, TRANSACTION_COOKIE, type OidcTransaction } from "@/lib/oidc";
import { generators } from "openid-client";

export const runtime = "nodejs";

export async function GET(request: NextRequest) {
  try {
    assertOidcConfiguration();
    const redirectUri = callbackUri(request.url);
    const client = await getOidcClient(redirectUri);
    const transaction: OidcTransaction = {
      state: generators.state(),
      nonce: generators.nonce(),
      codeVerifier: generators.codeVerifier(),
    };
    const response = NextResponse.redirect(authorizationUrl(client, redirectUri, transaction));
    response.cookies.set(TRANSACTION_COOKIE, encryptCookieValue(transaction), cookieOptions(600));
    return response;
  } catch {
    return NextResponse.json({ detail: "Cognito sign-in is not configured or available." }, { status: 503 });
  }
}