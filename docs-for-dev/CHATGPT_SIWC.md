# Sign in with ChatGPT (SIWC)

This source checkout adds an independent **ChatGPT (SIWC)** provider using the
official open-source public-client flow. Existing OpenAI API-key and Codex OAuth
connections remain available.

## Connect on this computer

Install the source checkout using [Agent Setup](AGENT_SETUP.md). The new core
dependency is `PyJWT[crypto]>=2.10,<3.0`; install the updated requirements.

1. Run DeepTutor locally and open Settings → Providers → Add provider.
2. Choose **ChatGPT (SIWC)** and click **Continue with ChatGPT**.
3. Sign in on `auth.openai.com`, choose the appropriate account/workspace and
   authorize ChatGPT plan usage. The callback returns to `127.0.0.1`.
4. On success the account's visible model catalog is added to Settings. Select
   its model as your active LLM. If no LLM was selected, the first discovered
   model becomes active. Refresh models if discovery temporarily fails.
5. **Manage usage** opens <https://chatgpt.com/settings/usage>.

CLI users can run `deeptutor provider login openai-chatgpt`. When saved
registrations exist, the CLI lets you choose one or add another. Run it with the
same `DEEPTUTOR_HOME` as the application, with the server stopped.

This implementation supports a local single-process server, including the
local Docker deployment below. SIWC lifecycle requests go through the same-origin
frontend proxy to the backend's loopback interface. Direct remote lifecycle API
access is rejected. Distributed workers need a separate deployment design.

## Local Docker deployment

Use this modified source tree; upstream prebuilt images do not contain SIWC.
With Docker Desktop running in Linux-container mode (or local Docker Engine):

```sh
mkdir -p data/user/workspace/outputs data/cli-apps
docker compose -f compose.siwc.yaml up -d --build
docker compose -f compose.siwc.yaml ps
```

In PowerShell, use `New-Item -ItemType Directory -Force data/user/workspace/outputs,data/cli-apps`
for the directory preparation. Open <http://127.0.0.1:3782> and use the normal
SIWC settings card. No Python/Node installation on the host is required.

The Compose file builds the app and isolated sandbox runner from source.
It publishes the frontend and callback on the **host's loopback interface only**;
the backend and runner are not published. All persistent app state stays under
`./data`, including the stable SIWC host ID and credentials. Keep `backend_workers`
at its default value of 1 in `data/user/settings/system.json`.

For another host drive, set both `DEEPTUTOR_DATA_HOST` (application data and
read-only runner CLI apps) and `DEEPTUTOR_WORKSPACE_HOST` (shared workspace) in
the checkout's ignored `.env`. For example, use `D:/DeepTutor/data` and
`D:/DeepTutor/data/user/workspace`. Stop the services and copy existing data
before changing these paths, then start with `up -d --no-build`. Docker Desktop's
own image/volume disk location is configured separately in its Resources settings.

Inside the container, the callback listener binds `0.0.0.0:1468`; the browser
still receives `http://127.0.0.1:1468/auth/callback`, as required by SIWC.
These settings are explicit transport environment variables:

- `DEEPTUTOR_SIWC_CALLBACK_LISTEN_HOST`: defaults to `127.0.0.1` outside Docker.
- `DEEPTUTOR_SIWC_CALLBACK_PORT`: defaults to an available port outside Docker;
  Compose uses 1468. If occupied, set this variable to another free port before
  invoking Compose. Host and container port change together.

Docker runs Linux, so credential files use owner-only Unix permissions. Do not
copy the Windows DPAPI credential file into the container; sign in separately
for the Docker runtime. Do not run the native app and container simultaneously
against the same data directory.

```sh
docker compose -f compose.siwc.yaml logs -f deeptutor
docker compose -f compose.siwc.yaml down
```

The stop command retains `./data`. For custom application ports already saved
in `system.json`, use the repository's `scripts/docker_compose.py` wrapper with
`-f compose.siwc.yaml` so host mappings follow those settings.

## Behavior and storage

- Registration starts with `dynamic_agent_client`, a persisted UUID host URI,
  fresh state, nonce and S256 PKCE. Later sign-ins reuse the issued client ID.
- The backend verifies the ID token's signature using OpenAI discovery/JWKS,
  issuer, audience, expiry, subject and nonce. Returning registrations must
  preserve their verified identity and client ID.
- Identity-only consent is retained, but cannot make inference requests without
  `chatgpt.tokens.use.direct`. Enable plan usage by signing in again.
- Credentials live in the owner's existing private secrets tree, under
  `private/openai-chatgpt/accounts.v1.json`, outside sandbox-visible workspaces.
  Windows encrypts this file with current-user DPAPI; Unix uses owner-only
  directory/file modes. Writes are atomic and refresh uses a cross-process file
  lock, replacing rotating credentials together. Windows encrypted files cannot
  simply be copied to a different OS account; sign in again there.
- No credentials are stored in the frontend, model catalog, `.env`, or Codex
  login files. Optional OAuth ID-token hints are omitted.
- Each published model carries its registration ID, so selecting an older
  account's model cannot silently use the last account that signed in.
- Sign-out revokes the renewable session before clearing local tokens. It
  retains account/client/host mapping for reauthorization. An unconfirmed remote
  revocation is reported explicitly. Sign-out is refused during active requests.
- Model calls go only to `https://api.openai.com/v1/responses` with `store:false`
  and `stream:true`. Tool functions are grouped in the `deeptutor` namespace.
  Full conversation input is replayed, including reasoning state when returned.
  Failed, incomplete and interrupted streams are distinguished from completion.
  There is no fallback to API-key billing.

## Limits

Account eligibility, model availability, regional/workspace policy and usage
limits are determined by OpenAI. Real sign-in and a successful model request
must be tested with the user's eligible account; simulated tests do not establish
account access.

SIWC does not provide embeddings, image/video generation, transcription, hosted
file search or other unsupported endpoints. Configure separate providers for
DeepTutor features requiring those services. GraphRAG/PageIndex indexing that
requires an API-key profile continues to require one.

## Verification

Backend tests use synthetic RSA-signed identities, mocked OpenAI endpoints and
real local HTTP callbacks. They cover invalid states/identity claims, consent
decline, client/account mismatch, serialized refresh, revocation, protected
storage, model/profile ownership, both provider factories and stream completion.
Frontend tests cover browser authorization, pending settings, sign-out feedback
and provider settings integration.

```powershell
python -m pytest tests/services/test_chatgpt_siwc.py -q
cd web
npm run typecheck
npx vitest run tests/chatgpt-siwc.spec.tsx tests/provider-registry-workflow.spec.tsx
```

Official contracts used:

- <https://developers.openai.com/siwc/token-sharing-open-source/sign-in>
- <https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions>
- <https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference>
- <https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations>
