# Plan: running jaynes on melville

Status: **built and running on melville.** The text below is the plan it was built from; where the scripts differ, the scripts are current. Jaynes is private and has no authentication, so it runs on a machine on the local network (melville) and is not hosted online. No cloud server is needed. An earlier version of this plan proposed a small cloud VM; that was dropped.

## What runs where

- **Development:** on the laptop, in this repository. Tests run there with no network.
- **Running it:** on melville, as one FastAPI process that serves both the API and the built React app on one port, with in-memory sessions. It calls the Gemini API (actor) and the Anthropic API (observer) over the internet and needs no database.
- **Using it:** from the laptop, through an SSH port-forward to melville, so the app never listens on the network.

Consequences:

- **One worker only.** The event log and its fan-out are in process memory, so the server is a single process.
- **A restart ends every conversation.** The deploy script refuses to restart while a turn is in progress.
- **No authentication.** The server binds to `127.0.0.1` on melville, never to the network address.

## What melville has and lacks (checked over SSH, read-only)

| | Finding | Consequence |
| --- | --- | --- |
| System | macOS 10.15.8, Intel, 32 GB memory, plenty of disk | Plenty of room |
| Python | Homebrew `python3.12` (3.12.1) at `/usr/local/bin`; the default `python3` is 3.8.2 | Use 3.12 explicitly for a virtualenv; do not use the default |
| Node | Not installed | Build `web/` on the laptop and upload `web/dist`; nothing to install on melville |
| Docker | Not installed | Run directly from a virtualenv under `launchd`, not in a container |
| Homebrew | Present (old macOS, so some packages may not install) | Avoid depending on it for anything new |
| Admin rights | `sudo` needs a password | Everything lives in the user's home directory; no system-wide changes |

Not yet checked: whether the laptop can reach melville away from the home network. Melville's sleep setting shows "never" (`pmset`), although it may sleep in practice.

## Layout on melville

```
~/jaynes/             a clone of this repository (code only)
~/jaynes/.venv        virtualenv from python3.12
~/jaynes/web/dist     built frontend, uploaded by the deploy script (not in git)
~/.config/jaynes/env  the two API keys, readable only by the user (mode 600)
~/Library/LaunchAgents/jaynes.plist   keeps the server running
~/Library/Logs/jaynes.log             output of the server
```

## Secrets

The two keys (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) live in `~/.config/jaynes/env` on melville, mode 600, and are never in the repository or the plist. A small script prompts for each value so a key never appears in a command line or in shell history. The server logs which side failed (`actor` or `observer`), never a key.

## Running as a service

A `launchd` agent starts the server at login and restarts it if it dies. It runs `uvicorn` with a single worker, bound to `127.0.0.1:8000`, reading the keys from the env file. A `/healthz` route reports that the server is up and how many turns are in progress.

## Using it from the laptop

```
ssh -L 8000:localhost:8000 melville
```

then open `http://localhost:8000`. Only someone who can SSH to melville can reach it. An alias (like `payer-ssh`) can wrap this.

## Deploy script

`scripts/update-jaynes.sh [<sha>]` (default `origin/main`), run from the laptop:

1. Check `/healthz` on melville; stop if a turn is in progress (unless forced).
2. Build `web/` on the laptop (`npm ci && npm run build`).
3. On melville: fetch, check out the commit, install dependencies into the virtualenv.
4. Upload `web/dist` to melville.
5. Restart the `launchd` service and wait for `/healthz`.
6. Print the deployed commit. `--status` shows what is deployed and whether it is answering.

Rollback is the same script with an earlier commit.

## Monitoring and limits

- No rate limits or spend ceiling in the app (it is private). Set **budget alerts** with Anthropic and Google AI so a runaway loop shows up quickly.
- Logs are in `~/Library/Logs/jaynes.log`.

## Cost

No server cost. Only model usage:

- Haiku 5.5 was priced by third-party trackers at about $0.10 per million input tokens and $0.50 per million output tokens (unconfirmed against Anthropic's own page). The observer reads the whole transcript on every turn, so its cost grows with conversation length. A turn of about 3,000 tokens in and 300 out is roughly $0.0005.
- Gemini flash-lite usage is not priced here; check it when building.

## Order of work

1. Write the backend and frontend from the design brief, with tests, and run them on the laptop first.
2. Prepare melville: clone, virtualenv with Python 3.12, env file with the keys.
3. Write the `launchd` agent and the deploy script; first deploy; check through the SSH forward.
4. Set budget alerts.

## Verification

- `/healthz` answers through the forward; the app does not answer on melville's network address.
- A message streams a reply immediately; a retracted draft is marked contested and one correction follows.
- Reconnecting the event stream mid-turn loses and duplicates nothing.
- The deploy script refuses to restart during a turn, and rolls back with an older commit.
- The service comes back after melville restarts or the server crashes.
- No key in the repository, the process list or the logs.

## Risks

- **Lost conversations on restart** are by design for now. If that becomes a nuisance, SQLite on melville's disk is the next step.
- **Melville asleep** is acceptable: sleeping suspends the server and its conversations survive, but a reply in progress when it sleeps fails and the next SSH connection may need a moment (or a second try) while melville wakes. A shutdown or reboot ends the conversations.
- **Old macOS (10.15):** some current Python packages or Homebrew formulae may not support it. Verify the dependencies install before relying on it.
- **Server-sent events through an SSH forward** should work, but have not been tested.
- **Model IDs and SDK signatures** in the brief are unverified and must be checked against current documentation when the code is written.
