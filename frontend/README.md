This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

## Cognito setup

Configure these server-side frontend environment variables before building or running the app:

```env
COGNITO_USER_POOL_ID=ap-south-2_Z65eBsA6F
COGNITO_CLIENT_ID=71pp1273ea6t7muvmujqcvabcn
COGNITO_REGION=ap-south-2
COGNITO_HOSTED_UI_DOMAIN=https://ap-south-2z65ebsa6f.auth.ap-south-2.amazoncognito.com
AUTH_SESSION_SECRET=generate-a-random-secret-at-least-32-bytes
ASTRA_BACKEND_URL=http://localhost:8003
```

`COGNITO_HOSTED_UI_DOMAIN` is the Cognito managed-login domain, not the CloudFront frontend URL. In the existing Cognito app client, enable authorization code grant and the `openid`, `email`, and `profile` scopes; use a public client with no client secret. Add `http://localhost:3000/auth/callback` and `http://localhost:3000/login` as local callback/logout URLs. For the deployed site, add `https://d84l1y8p4kdic.cloudfront.net/auth/callback` and `https://d84l1y8p4kdic.cloudfront.net/login`.

Generate `AUTH_SESSION_SECRET` with a cryptographically secure random value (at least 32 bytes) and configure it as a server-only deployment secret. Do not put it in `NEXT_PUBLIC_*` variables or DynamoDB. `ASTRA_BACKEND_URL` is the backend origin used by the same-origin authenticated API proxy.

This app uses `openid-client` authorization code flow with S256 PKCE. Cognito user provisioning remains administrator-managed: link each user's Cognito `sub` as `auth.cognito_user_id` and set `access_role` on the Astra employee record. A first manager must be linked and marked `MANAGER` before management operations can be used. Astra does not store passwords or an OAuth client secret.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
