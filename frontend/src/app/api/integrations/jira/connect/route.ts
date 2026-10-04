import { NextRequest, NextResponse } from "next/server";

import { cookieOptions, decryptCookieValue, encryptCookieValue, SESSION_COOKIE, type AuthSession } from "@/lib/oidc";

export const runtime = "nodejs";

const JIRA_STATE_COOKIE = "astra_jira_oauth_state";

function jiraAuthorizationUrl() {
  const clientId = process.env.JIRA_CLIENT_ID;
  const redirectUri = process.env.JIRA_CALLBACK_URL;
  if (!clientId || !redirectUri) {
    throw new Error("Jira OAuth is not configured");
  }

  const state = crypto.randomUUID().replace(/-/g, "");
  const authorizationUrl = new URL("https://auth.atlassian.com/authorize");
  authorizationUrl.searchParams.set("audience", "api.atlassian.com");
  authorizationUrl.searchParams.set("client_id", clientId);
  authorizationUrl.searchParams.set("scope", "read:jira-user read:jira-work offline_access");
  authorizationUrl.searchParams.set("redirect_uri", redirectUri);
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("prompt", "consent");

  return { authorizationUrl: authorizationUrl.toString(), state, redirectUri };
}

export async function GET(request: NextRequest) {
  const session = decryptCookieValue<AuthSession>(request.cookies.get(SESSION_COOKIE)?.value);
  if (!session) {
    return NextResponse.json({ detail: "Authentication required." }, { status: 401 });
  }

  try {
    const { authorizationUrl, state, redirectUri } = jiraAuthorizationUrl();
    const returnTo = request.nextUrl.searchParams.get("returnTo");
    const safeReturnTo = returnTo === "/dashboard/projects" ? returnTo : "/dashboard";
    const response = NextResponse.redirect(authorizationUrl);
    response.cookies.set(
      JIRA_STATE_COOKIE,
      encryptCookieValue({ state, redirectUri, returnTo: safeReturnTo }),
      cookieOptions(60 * 10),
    );
    return response;
  } catch {
    return NextResponse.json({ detail: "Jira OAuth is not configured or available." }, { status: 503 });
  }
}
