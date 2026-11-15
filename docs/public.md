# Serving Jaynes to the public

Jaynes was built to be reached through an SSH tunnel. Serving it on the internet changes what it has to withstand, mostly in money: every message costs at two model providers, and registration is open.

## What stands between a stranger and your API keys

- **Ceilings** (`limits.py`, settings in `.env.example`; 0 switches one off): messages per hour per user (30), estimated tokens per day per user (300,000), estimated tokens per day for everyone (3,000,000), new accounts per day (50). Days are UTC. When the everyone-total is reached, Jaynes answers `503` "resting until midnight UTC" and makes no model calls. `/healthz` on the machine shows `usage_today`.
- **Estimates, not metering.** A turn's tokens are the characters sent to and received from the models (the actor's draft and each observer review, including attached files) divided by four. Set budget alerts with Google and Anthropic as well: they are the backstop.
- **Logins and sign-ups** keep their throttles (5 failed logins per email and 30 per address per 15 minutes; 20 sign-ups per address per hour). They only work per visitor when the address is the visitor's, so run behind the proxy with `BEHIND_PROXY=1`.
- **Requests**: a body larger than `MAX_UPLOAD_BYTES` plus 1 MB is refused before it is read (Caddy refuses at 12 MB first). Files are read in memory for one request and are not kept.
- **Headers**: a strict Content-Security-Policy (own origin only, no framing), `nosniff`, `Referrer-Policy: no-referrer`, and HSTS when `COOKIE_SECURE=1`.
- **`/healthz`** says only `{"ok": true}` to a request that came through the proxy; the tools, sessions and spending are for the operator on the machine.

## What the ceilings do not do

They limit money, not content. A stranger can still ask Jaynes anything; the observer and the tool restrictions are the guardrails. Gmail, Drive, GitHub and `retrieve_docs` stay off (`SHARED_PRIVATE_TOOLS` unset), `python_sandbox` and `web_fetch` keep their own limits, and attaching a file turns web search and fetch off for the rest of that conversation. On Gemini's free tier Google may train on prompts. Set `USE_VERTEX=1` (the actor then calls Vertex AI as the VM's service account, billed to the project, under Google Cloud's paid terms) or put the API key's project on paid billing before inviting the public.

## Where it runs

`jaynes.cyberiad.ai` is `jaynes-vm` on GCP (an `e2-micro` in `us-west1-b`), defined in the `cyberiad-infra` repository (`envs/jaynes`). Caddy gets the certificate and faces the internet; Jaynes listens on `127.0.0.1:8000` under `systemd` as the `jaynes` user, which may write only to `/var/lib/jaynes` (the database and the tool-call audit log). Its settings come from Secret Manager (`jaynes-env`, written by `cyberiad-infra/scripts/add-jaynes-secrets.sh`) into tmpfs at each start. The database is backed up nightly at 03:30 UTC to the private bucket `cyberiad-demo-gcp-jaynes-backups` (kept 30 days). `python_sandbox` is off there: it needs macOS's `sandbox-exec`.

Deploy with `scripts/update-jaynes-gcp.sh [sha]` (builds `web/` here, installs over IAP, restarts; `--status`, `--restart`, `--force`). The old `scripts/update-jaynes.sh` still targets melville.

## Running it elsewhere

1. Jaynes on `127.0.0.1:8000` as today, with `BEHIND_PROXY=1` and `COOKIE_SECURE=1` in its environment file (`deploy/run.sh` adds the proxy flags).
2. Caddy with `deploy/Caddyfile` for the certificate and the https front. Ports 80 and 443 open; SSH only through the cloud provider's tunnel.
3. One worker only (sessions are in memory), unchanged.
4. Back up `DATA_DIR/jaynes.db` (`sqlite3 jaynes.db ".backup copy.db"`).
