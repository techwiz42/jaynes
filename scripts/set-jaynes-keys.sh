#!/usr/bin/env bash
# Asks for the two API keys (typed without echo) and writes them to ~/.config/jaynes/env on melville, readable only by its owner.
# The keys never appear in a command line, in shell history, or in this repository. Run it yourself, in a terminal.
set -euo pipefail
HOST=${JAYNES_HOST:-melville}
read -r -s -p "GEMINI_API_KEY: " GEMINI; echo
read -r -s -p "ANTHROPIC_API_KEY: " ANTHROPIC; echo
[ -n "$GEMINI" ] && [ -n "$ANTHROPIC" ] || { echo "both keys are needed"; exit 1; }
printf 'GEMINI_API_KEY=%s\nANTHROPIC_API_KEY=%s\n' "$GEMINI" "$ANTHROPIC" \
  | ssh -o BatchMode=yes "$HOST" 'umask 077 && mkdir -p ~/.config/jaynes && cat > ~/.config/jaynes/env && chmod 600 ~/.config/jaynes/env'
unset GEMINI ANTHROPIC
echo "keys written on $HOST. Start the service with: scripts/update-jaynes.sh --restart"
