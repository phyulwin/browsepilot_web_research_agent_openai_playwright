# BrowsePilot university demo deployment

Configuration is prepared, not deployed or runtime-verified. No tests were created or run. Execute the publication commands below only when you authorize deployment; they create billable resources. Use a current Google Cloud CLI, Docker Desktop running Linux containers, and Git Bash from the repository root on Windows (the commands below are Bash, not PowerShell).

## Architecture and limits

- GitHub Pages hosts the React build at `https://OWNER.github.io/browsepilot_web_research_agent_openai_playwright/`; existing hash routes (`#dashboard`, session hashes) work without a rewrite server. Do not use path-based session URLs.
- Cloud Run runs one Uvicorn worker, with service minimum/maximum instances set to one, one active research job, and HTTP concurrency 20 so SSE does not block approval, screenshots, cancellation, and reports.
- Research execution is capped at 1,200 seconds; planning is a separate phase. The Cloud Run request timeout is 1,500 seconds for SSE headroom. Existing action/model/token budgets remain unchanged, so tasks can finish earlier.
- `--no-cpu-throttling` enables instance-based billing and CPU outside HTTP requests; `--min=1` retains an instance after approval returns 202, including when the browser disconnects. A long HTTP timeout alone does **not** keep background work alive. See [Cloud Run billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings).
- SQLite, screenshot files, and stored report state stay under `/tmp/browsepilot`. There are **no database migrations, external databases, Cloud SQL instances, or Cloud Storage buckets**. Container files consume instance memory and disappear on replacement. Download reports before ending the demo.
- Tasks are in-process and **not durable**: crashes, maintenance, out-of-memory termination, restarts, or redeployment can interrupt them. Minimum instances and maximum one do not guarantee completion or exactly one physical instance during replacement. Do not split traffic, use tagged revisions, or redeploy during research. Session affinity is not relied on because cross-site cookies may be blocked.

## 1. Prepare Google Cloud

Use a dedicated demo project with billing enabled. The person running setup needs permission to enable APIs, create Artifact Registry repositories/service accounts/secrets, change their IAM policies, deploy Cloud Run, and act as the runtime service account. An organization policy that prohibits public Cloud Run invocation must be resolved by your university administrator before using this architecture.

```bash
# Set these values for your project and GitHub account/organization.
export PROJECT_ID="YOUR_GOOGLE_PROJECT_ID"
export REGION="us-central1"
export GITHUB_OWNER="YOUR_GITHUB_OWNER"
export SERVICE="browsepilot-demo"
export RUNTIME_SA="browsepilot-demo@${PROJECT_ID}.iam.gserviceaccount.com"
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/browsepilot-demo/backend:v1"

# Authenticate and enable only the services used by this deployment.
gcloud auth login
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com
gcloud artifacts repositories create browsepilot-demo --repository-format=docker --location="$REGION" --description="Temporary BrowsePilot demo images"
gcloud iam service-accounts create browsepilot-demo --display-name="BrowsePilot demo runtime"
```

In Google Cloud Console → **Secret Manager → Create secret**, name it `browsepilot-demo-env`, choose automatic replication, and enter this two-line dotenv content with your actual values:

```dotenv
# Server-only values: paste into Secret Manager, never into GitHub variables or source files.
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
ACCESS_PASSWORD=YOUR_UNIQUE_RANDOM_PASSWORD_AT_LEAST_32_CHARACTERS
```

Use a literal API key and a random alphanumeric password; do not use `${VARIABLE}` substitutions. This creates version `1`; the command below pins that version. Both secrets stay in Secret Manager, mounted read-only as `/secrets/browsepilot/env`; only this explicitly selected file supplies the OpenAI key, with no environment-key fallback. Local development still reads the root `.env`. The runtime identity needs access only to this specific secret:

```bash
# Grant the runtime identity access to this secret, not project-wide administrative roles.
gcloud secrets add-iam-policy-binding browsepilot-demo-env --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor
```

## 2. Build and deploy the backend when authorized

The production `cloudrun` Docker target installs pinned Python dependencies, matching Playwright Chromium and system packages, runs as a non-root user, and listens on `0.0.0.0:$PORT`. Its build skips the frontend. Build locally and push directly to Artifact Registry; no Cloud Build staging bucket is created.

```bash
# Build and publish the backend image; these commands do not execute tests.
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker buildx build --platform linux/amd64 --target cloudrun --tag "$IMAGE" --push .

# Deploy only when ready to start continuous instance billing.
# MSYS_NO_PATHCONV protects secret mount paths when using Git Bash on Windows.
MSYS_NO_PATHCONV=1 gcloud run deploy "$SERVICE" \
  --image="$IMAGE" --region="$REGION" --service-account="$RUNTIME_SA" \
  --allow-unauthenticated --execution-environment=gen2 \
  --port=8080 --cpu=1 --memory=2Gi --concurrency=20 \
  --no-cpu-throttling --min=1 --max=1 --max-instances=1 --timeout=1500s \
  --set-env-vars="APP_ENV=production,ALLOWED_ORIGINS=https://${GITHUB_OWNER}.github.io" \
  --set-secrets="/secrets/browsepilot/env=browsepilot-demo-env:1"

# Use one active revision and record its public HTTPS origin for the frontend.
gcloud run services update-traffic "$SERVICE" --region="$REGION" --to-latest
gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)'
```

Public Cloud Run invocation permits the browser to reach the API; protected endpoints still require BrowsePilot workspace authentication. CORS permits only the configured GitHub origin, **without** the repository path or trailing slash. CORS is not an authentication mechanism. Secret rotation requires a new secret version and an authorized service update to that version; restart/redeploy between sessions because settings are loaded at startup.

Cloud Run does not provide this project's custom Docker seccomp/user-namespace setup. The cloud target explicitly sets `CHROMIUM_SANDBOX=false`; the local/default Docker sandbox remains enabled. This reduces Chromium's internal isolation while retaining non-root execution, Cloud Run isolation, and the application's public-address/read-only proxy. Use only for the trusted, password-protected university demo, not a public multi-tenant browser service. See [Google's browser automation guidance](https://docs.cloud.google.com/run/docs/browser-automation).

## 3. Configure GitHub Pages when publication is authorized

1. Push the reviewed changes to `browsepilot_web_research_agent_openai_playwright` on `main` (adjust the workflow branch if yours differs).
2. In **Settings → Pages**, select **GitHub Actions** as the source. The repository must have a plan/visibility that supports Pages.
3. In **Settings → Secrets and variables → Actions → Variables**, add `VITE_API_URL` with the exact Cloud Run HTTPS origin printed above, without `/api` or a trailing slash. This URL is public; never put the API key or workspace password in any `VITE_*` value.
4. Add repository variable `DEPLOY_PAGES=true` only when you authorize frontend publication. Until enabled, the workflow is skipped even on push. Optional environment reviewers can gate the `github-pages` environment.
5. Run **Actions → Deploy frontend to GitHub Pages → Run workflow**. Future pushes to `main` deploy automatically while `DEPLOY_PAGES=true`. The workflow builds the frontend only; it never deploys the backend.
6. Open `https://OWNER.github.io/browsepilot_web_research_agent_openai_playwright/` and sign in with the Secret Manager workspace password. Share this password privately with demo participants.

The workflow supplies Vite's repository base path and production API origin at build time. Vite rebases CSS/public assets and fonts; the hero image uses `BASE_URL`. Hash navigation avoids GitHub Pages deep-link 404s; a separate SPA 404 rewrite is unnecessary. Changes to `VITE_API_URL` require a new frontend build/deployment. See [Vite's Pages deployment guidance](https://vite.dev/guide/static-deploy.html).

## Authentication, screenshots, exports, and SSE

Login exchanges the workspace password for an eight-hour signed bearer token stored in tab-scoped `sessionStorage`. Cross-origin API calls send `Authorization`, not third-party cookies; local same-origin cookies still work. All participants share the same demo workspace. Logout clears the browser's token/cookie; copied tokens remain valid until expiry or password rotation, so do not share browser storage or logs containing tokens.

Screenshots and Markdown exports use authenticated fetch and temporary blob URLs, with no credentials in URLs. SSE uses authenticated streaming fetch, reconnects after interruption, and receives a full snapshot on reconnect. A 401 or missing session stops reconnection; sign in again after expiry, or start new research after ephemeral data loss. Explicit CORS preflights permit GET/POST and Authorization/Content-Type; errors also receive CORS headers.

## Costs and stopping the demo

The minimum warm instance with instance-based billing incurs CPU and memory charges while idle. Other possible charges include Artifact Registry image storage, Secret Manager versions/accesses, network egress, logging, and OpenAI API usage; GitHub Actions/Pages allowances depend on the account. Free tiers or university credits may offset charges but are not guaranteed. See [Cloud Run pricing](https://cloud.google.com/run/pricing). Set Google Cloud billing-budget alerts and OpenAI project spending controls; alerts are not automatic spending caps. Do not leave the demo running overnight unintentionally.

At the published us-central1 instance-based rates checked November 9, 2026 ($0.000018/vCPU-second and $0.000002/GiB-second), this 1-vCPU/2-GiB instance is approximately **$0.0792/hour or $1.90/day**, before free-tier offsets and all other charges; this is a compute estimate, not a spending cap ([pricing source](https://cloud.google.com/run/pricing)). Keep the minimum at one during the demo, then delete the service afterward.

```bash
# After downloading reports and ending sessions, remove the running demo service.
gcloud run services delete "$SERVICE" --region="$REGION"

# Optional: delete this dedicated demo's stored images and secret after you no longer need them.
gcloud artifacts repositories delete browsepilot-demo --location="$REGION"
gcloud secrets delete browsepilot-demo-env
gcloud iam service-accounts delete "$RUNTIME_SA"
```

Disable `DEPLOY_PAGES` to stop future automatic frontend publications; use **Settings → Pages → Unpublish site** to remove the published frontend. Keeping a static frontend published does not stop backend billing. Deleting the service permanently loses its temporary research data; the commands above leave deletion confirmation prompts enabled.

## Local development remains available

Leave frontend `VITE_API_URL` blank and `VITE_BASE_PATH=/` (see `frontend/.env.example`); Vite proxies `/api` locally. Keep your existing root `.env` and Windows `python -m backend.run` command. The default Docker target still includes the frontend and works with the existing Compose persistent local volume; only the explicitly selected `cloudrun` target uses temporary storage and its mounted secret configuration. No runtime validation of this deployment has been performed.
