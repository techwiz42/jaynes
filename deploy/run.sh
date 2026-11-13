#!/bin/sh
# What launchd runs on melville: the keys from ~/.config/jaynes/env, then one server process, on localhost only.
set -eu
set -a
. "$HOME/.config/jaynes/env"
set +a
cd "$HOME/jaynes/app"
exec "$HOME/jaynes/.venv/bin/uvicorn" bicameral.server:app --host 127.0.0.1 --port 8000 --workers 1
