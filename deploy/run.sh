#!/bin/sh
# What launchd runs on melville: the keys from ~/.config/jaynes/env, then one server process, on localhost only.
set -eu
set -a
. "$HOME/.config/jaynes/env"
set +a
export DATA_DIR="${DATA_DIR:-$HOME/jaynes/data}"        # users and conversations live beside the code checkout, not in it, so a redeploy never touches them
mkdir -p "$DATA_DIR" && chmod 700 "$DATA_DIR"                          # the folder is private too: other users of this machine cannot even list it
cd "$HOME/jaynes/app"
PROXY=""
if [ "${BEHIND_PROXY:-0}" = "1" ]; then                                 # a reverse proxy (Caddy) on this machine faces the internet: take the client's address from it, so the per-address limits tell visitors apart
  PROXY="--proxy-headers --forwarded-allow-ips 127.0.0.1"
fi
# shellcheck disable=SC2086
exec "$HOME/jaynes/.venv/bin/uvicorn" bicameral.server:app --host 127.0.0.1 --port 8000 --workers 1 $PROXY
