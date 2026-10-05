# GitHub App Setup

## GitHub App

Create a GitHub App with:

- Repository permissions: `Metadata: read-only`, `Contents: read-only`, `Pull requests: read-only`, and `Issues: read-only`.
- Setup URL: `https://<public-astra-api-host>/api/v1/integrations/github/callback`.
- Webhook URL: not required for repository discovery or read-only evidence fetching.

The callback accepts GitHub's `installation_id`, `setup_action`, and signed `state`; state expires after ten minutes and binds installation to the authenticated Astra employee who initiated setup. Local development requires a public HTTPS tunnel to the backend callback because GitHub cannot call `localhost`.

## Backend Configuration

These credentials are read only from the backend environment. Never add them to frontend environment files or return them from API routes.

```dotenv
GITHUB_APP_ID=123456
GITHUB_CLIENT_ID=Iv1...
GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_FRONTEND_URL=http://localhost:3000
```

The installation slug is fetched from GitHub's authenticated `/app` metadata endpoint and the returned client ID is checked against `GITHUB_CLIENT_ID`. The signed OAuth state key is derived inside the backend from the App private key and client ID; no additional browser-visible secret is used. For SAM deployments, pass the `GitHubAppId`, `GitHubClientId`, and `GitHubPrivateKey` parameters securely, and set `AstraFrontendUrl` to the frontend origin, for example `https://astra.example.com`.

## Astra Storage

Employee records may store GitHub identity as `github.user_id` and `github.login` for later evidence attribution; GitHub App installations are not stored on employees. Project records store the selected repository's provider, organization, name, repository ID, default branch, and installation ID.

The repository-scoped pull request, commit, and issue endpoints return normalized evidence with stable GitHub identifiers and employee attribution. They do not write to `AstraProjectState` or change Astra task/project state. The referenced Evidence Engine is not implemented in the backend yet; these normalized responses are ready for that future processor.