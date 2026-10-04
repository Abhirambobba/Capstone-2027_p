import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { generators, Issuer, type Client, type TokenSet } from "openid-client";

export const SESSION_COOKIE = "astra_session";
export const TRANSACTION_COOKIE = "astra_oidc_transaction";

export type AuthSession = {
  accessToken: string;
  subject: string;
  expiresAt: number;
};

export type OidcTransaction = { state: string; nonce: string; codeVerifier: string };

const clientCache = new Map<string, Promise<Client>>();

function requiredSetting(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} must be configured`);
  return value;
}

export function assertOidcConfiguration(): void {
  requiredSetting("COGNITO_USER_POOL_ID");
  requiredSetting("COGNITO_CLIENT_ID");
  requiredSetting("COGNITO_REGION");
  requiredSetting("COGNITO_HOSTED_UI_DOMAIN");
  encryptionKey();
}

function encryptionKey(): Buffer {
  const secret = requiredSetting("AUTH_SESSION_SECRET");
  if (Buffer.byteLength(secret) < 32) throw new Error("AUTH_SESSION_SECRET must be at least 32 bytes");
  return createHash("sha256").update(secret).digest();
}

export function cognitoClientId(): string {
  return requiredSetting("COGNITO_CLIENT_ID");
}

export function cognitoHostedDomain(): string {
  const raw = requiredSetting("COGNITO_HOSTED_UI_DOMAIN");
  const url = new URL(raw.includes("://") ? raw : `https://${raw}`);
  return url.origin;
}

export function callbackUri(requestUrl: string): string {
  return new URL("/auth/callback", requestUrl).toString();
}

export async function getOidcClient(redirectUri: string): Promise<Client> {
  const region = requiredSetting("COGNITO_REGION");
  const poolId = requiredSetting("COGNITO_USER_POOL_ID");
  const clientId = cognitoClientId();
  const cacheKey = `${region}:${poolId}:${clientId}:${redirectUri}`;
  let clientPromise = clientCache.get(cacheKey);
  if (!clientPromise) {
    const issuerUrl = `https://cognito-idp.${region}.amazonaws.com/${poolId}`;
    clientPromise = Issuer.discover(issuerUrl).then(
      (issuer) => new issuer.Client({
        client_id: clientId,
        token_endpoint_auth_method: "none",
        redirect_uris: [redirectUri],
        response_types: ["code"],
      }),
    );
    clientCache.set(cacheKey, clientPromise);
  }
  return clientPromise;
}

export function encryptCookieValue(value: unknown): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), iv);
  const encrypted = Buffer.concat([
    cipher.update(JSON.stringify(value), "utf8"),
    cipher.final(),
  ]);
  return [iv, cipher.getAuthTag(), encrypted]
    .map((part) => part.toString("base64url"))
    .join(".");
}

export function decryptCookieValue<T>(value: string | undefined): T | null {
  if (!value) return null;
  try {
    const [encodedIv, encodedTag, encodedData] = value.split(".");
    if (!encodedIv || !encodedTag || !encodedData) return null;
    const decipher = createDecipheriv(
      "aes-256-gcm",
      encryptionKey(),
      Buffer.from(encodedIv, "base64url"),
    );
    decipher.setAuthTag(Buffer.from(encodedTag, "base64url"));
    const plaintext = Buffer.concat([
      decipher.update(Buffer.from(encodedData, "base64url")),
      decipher.final(),
    ]).toString("utf8");
    return JSON.parse(plaintext) as T;
  } catch {
    return null;
  }
}

export function cookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export function sessionFromTokenSet(tokenSet: TokenSet, previous?: AuthSession): AuthSession {
  const subject = tokenSet.claims()?.sub ?? previous?.subject;
  if (!subject) throw new Error("Cognito token did not include a subject");
  const accessToken = tokenSet.access_token ?? previous?.accessToken;
  if (!accessToken) throw new Error("Cognito token response did not include an access token");
  return {
    accessToken,
    subject,
    expiresAt: tokenSet.expires_at ?? Math.floor(Date.now() / 1000) + (tokenSet.expires_in ?? 3600),
  };
}

export async function refreshSession(session: AuthSession): Promise<AuthSession> {
  if (session.expiresAt > Math.floor(Date.now() / 1000) + 60) return session;
  throw new Error("Cognito session expired");
}

export function authorizationUrl(client: Client, redirectUri: string, transaction: OidcTransaction): string {
  return client.authorizationUrl({
    redirect_uri: redirectUri,
    scope: "openid email profile",
    response_type: "code",
    state: transaction.state,
    nonce: transaction.nonce,
    code_challenge: generators.codeChallenge(transaction.codeVerifier),
    code_challenge_method: "S256",
  });
}