# Plan: server and deployment

Status: **proposal, nothing created yet.** Each step that creates or changes a cloud resource needs approval when it is run. Costs are estimates from memory of Google's list prices (the pricing page could not be read when this was written); check them before relying on them.

## What is being deployed

One small FastAPI process that serves both the API and the built React app, on one port, with in-memory sessions. It calls the Gemini API (actor) and the Anthropic API (observer) over the internet and needs no database.

Consequences that shape everything below:

- **One worker only.** The event log and its fan-out are in process memory, so the server runs as a single process (`--workers 1`).
- **A restart ends every conversation.** A redeploy or reboot wipes all sessions. The deploy script refuses to restart while a turn is in progress.
- **No authentication.** The service must never be reachable from the internet.

## Decisions to confirm before building

| # | Decision | Proposal | Alternative |
| --- | --- | --- | --- |
| 1 | GCP project | The existing project (shared IAP setup, one bill, one set of SSH users) | A new project for clean separation of billing and access |
| 2 | Machine | `e2-small` (2 GB): the frontend can be built on the server | `e2-micro` (1 GB), with the frontend built elsewhere and shipped as static files |
| 3 | Region and zone | `us-west1-b`, as the other servers | Whatever is nearest the people using it |
| 4 | Access | SSH port-forward through IAP; the app listens only on localhost; no public URL | A public HTTPS name with a login (needs code the brief puts out of scope) |
| 5 | Names | Server `jaynes-vm`, service `jaynes`, secrets `jaynes-gemini-api-key` and `jaynes-anthropic-api-key` | Your choice |

## Server

- Compute Engine VM, Debian 13, 20 GB standard disk, Docker installed.
- **Network:** no inbound firewall rules except SSH from Google's IAP range. The VM has an ordinary external address only so it can reach the two model APIs; with no inbound rule allowing the app's port, nothing can reach the app from outside. (A VM with no external address would need Cloud NAT for outbound access, which costs about as much as the address.)
- **Service account:** a dedicated one with read access to the two secrets and nothing else (no project-wide roles).
- **Access:** `gcloud compute ssh jaynes-vm --zone=us-west1-b --tunnel-through-iap -- -L 8000:localhost:8000`, then open `http://localhost:8000`. Only people who can SSH to the VM can reach the app. An alias like the other servers' can wrap this.

## Secrets

The two API keys live in Secret Manager. The service fetches them into `/run/jaynes/env` (a file only root can read, on a tmpfs) each time it starts, so they never sit on disk or in the repository or the image. Keys are added with a script that prompts for the value, so a key never appears in a command line or in shell history.

## Application runtime

- A Docker image built from the repo, tagged with the commit and as `current`, with the previous image kept as `previous`.
- Multi-stage build when the frontend is built on the server: Node builds `web/`, and the final image holds only Python and the built files.
- Run by a systemd unit that starts the container read-only, with a small tmpfs, all capabilities dropped, no new privileges, a memory limit (about 600 MB), and the port bound to `127.0.0.1:8000` only.
- A `/healthz` route reports that the server is up and how many turns are in progress.

## Deploy script

`scripts/update-jaynes.sh [<sha>]` (default: `origin/main`), run from a laptop:

1. Check `/healthz`; stop if a turn is in progress (unless forced).
2. On the VM: fetch, check out the commit, build the image, tag the old one `previous`.
3. Restart the service and wait for `/healthz`.
4. Print the deployed commit. `--status` shows what is deployed and whether it is answering.

Rollback is the same script with an earlier commit.

## Monitoring and limits

- The app has no rate limits or spend ceiling (it is private). Instead, set **budget alerts** with Google Cloud billing, Anthropic and Google AI, so a runaway loop shows up quickly.
- `journalctl -u jaynes` has the logs; log text should name the failing side (`actor` or `observer`) but never the key.

## Cost per month (estimates)

| Item | e2-small | e2-micro |
| --- | --- | --- |
| VM, running all the time | about $12 | about $6 |
| 20 GB disk | about $1–2 | about $1–2 |
| External address | about $3–4 | about $3–4 |
| Secret Manager | cents | cents |
| **Infrastructure total** | **about $15–18** | **about $10–12** |

Plus model usage. Haiku 5.5 was priced by third-party trackers at about $0.10 per million input tokens and $0.50 per million output tokens (unconfirmed against Anthropic's own page). The observer reads the whole transcript on every turn, so its cost grows with conversation length. A turn of about 3,000 tokens in and 300 out is roughly $0.0005. Gemini flash-lite usage is not priced here; check it when building.

A free e2-micro is available in some regions, but another server of this size probably already uses that allowance.

## Order of work

1. Confirm the five decisions above.
2. Backend and frontend from the design brief, with tests, built and run locally first. (Separate from this plan.)
3. Create the server, firewall rule, service account and secrets (needs approval for each).
4. Add the keys, install Docker, write the systemd unit and deploy script.
5. First deploy; check through the tunnel.
6. Set budget alerts.

## Verification

- `/healthz` answers through the tunnel; the app does not answer on the VM's external address.
- A message streams a reply immediately; a retracted draft is marked contested and one correction follows.
- Reconnecting the event stream mid-turn loses and duplicates nothing.
- The deploy script refuses to restart during a turn, and rolls back with an older commit.
- No key in the repository, the image, the process list or the logs.

## Risks

- **Lost conversations on restart** are by design for now; if that becomes a nuisance, persistence is the next feature (SQLite on the disk would be enough).
- **1 GB machine:** building the frontend there may run out of memory. That is why `e2-small` is the proposal.
- **Server-sent events through an SSH forward** should work, but has not been tested.
- **Model IDs and SDK signatures** in the brief are unverified and must be checked against current documentation when the code is written.
