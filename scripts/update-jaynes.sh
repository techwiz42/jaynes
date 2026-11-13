#!/usr/bin/env bash
# Puts a commit of this (public) repository on melville and (re)starts the service there.
#   update-jaynes.sh [<sha|ref>]     default origin/main; refuses while a reply is being written (add --force to go ahead)
#   update-jaynes.sh --restart       just (re)start the service, e.g. after the keys were set
#   update-jaynes.sh --status        what is deployed and whether it is answering
# Melville clones the code itself; web/ is built here from the same commit (melville has no Node) and uploaded. The commit must be pushed.
# Rollback: run it again with an earlier sha.
set -euo pipefail
HOST=${JAYNES_HOST:-melville}
REPO_URL=https://github.com/techwiz42/jaynes.git
cd "$(dirname "$0")/.."

ssh_host() { ssh -o BatchMode=yes "$HOST" "$@"; }
health() { ssh_host 'curl -s -m 5 http://127.0.0.1:8000/healthz' 2>/dev/null || true; }

ARG=origin/main
FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) ARG=$a ;;
  esac
done

if [ "$ARG" = "--status" ]; then
  ssh_host 'echo "deployed: $(git -C ~/jaynes/app log --oneline -1 2>/dev/null || echo nothing)"; echo "keys file: $([ -f ~/.config/jaynes/env ] && echo present || echo missing)"; echo "service: $(launchctl list | grep -q local.jaynes && echo loaded || echo not loaded)"'
  echo "healthz: $(health)"
  exit 0
fi

start_service() {
  ssh_host bash -s <<'REMOTE'
set -euo pipefail
if [ ! -f "$HOME/.config/jaynes/env" ]; then echo "keys not set yet: run scripts/set-jaynes-keys.sh, then scripts/update-jaynes.sh --restart"; exit 0; fi
PLIST="$HOME/Library/LaunchAgents/local.jaynes.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed "s|@HOME@|$HOME|g" "$HOME/jaynes/app/deploy/local.jaynes.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/local.jaynes" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"
for i in $(seq 1 30); do
  if curl -s -m 2 http://127.0.0.1:8000/healthz | grep -q '"ok":true'; then echo "answering: $(git -C "$HOME/jaynes/app" log --oneline -1)"; exit 0; fi
  sleep 1
done
echo "the service did not answer in 30 seconds; see ~/Library/Logs/jaynes.log"; tail -15 "$HOME/Library/Logs/jaynes.log" 2>/dev/null || true; exit 1
REMOTE
}

if [ "$FORCE" = 0 ] && health | grep -q '"turns_in_progress":[1-9]'; then
  echo "a reply is being written right now; try again in a moment, or add --force"; exit 1
fi

if [ "$ARG" = "--restart" ]; then start_service; exit $?; fi

git fetch -q origin
SHA=$(git rev-parse "$ARG^{commit}")
git branch -r --contains "$SHA" | grep -q . || { echo "$SHA is not pushed; melville can only fetch pushed commits"; exit 1; }
echo "deploying ${SHA:0:8}"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git archive "$SHA" web | tar -x -C "$WORK"
(cd "$WORK/web" && npm ci --silent && npm run build --silent)

ssh_host bash -s -- "$REPO_URL" "$SHA" <<'REMOTE'
set -euo pipefail
REPO_URL=$1; SHA=$2
mkdir -p "$HOME/jaynes" && cd "$HOME/jaynes"
[ -d app/.git ] || git clone -q "$REPO_URL" app
git -C app fetch -q origin
git -C app checkout -q --detach "$SHA"
[ -d .venv ] || /usr/local/bin/python3.12 -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -e app
echo "code at $(git -C app log --oneline -1)"
REMOTE

COPYFILE_DISABLE=1 tar -C "$WORK/web" -cf - dist | ssh_host 'rm -rf ~/jaynes/app/web/dist && mkdir -p ~/jaynes/app/web && tar -xf - -C ~/jaynes/app/web'
start_service
