#!/usr/bin/env bash
# Puts a commit of this (public) repository on jaynes-vm (GCP) and (re)starts the service there.
#   update-jaynes-gcp.sh [<sha|ref>]     default origin/main; refuses while a reply is being written (add --force to go ahead)
#   update-jaynes-gcp.sh --restart       just (re)start the service, e.g. after the settings secret changed
#   update-jaynes-gcp.sh --status        what is deployed and whether it is answering
# The VM clones the code itself; web/ is built here from the same commit (the VM has no Node) and uploaded. The commit must be pushed. Rollback: run it again with an earlier sha.
# Reaches the VM through IAP (no open SSH port). The settings come from Secret Manager (jaynes-env), written by cyberiad-infra/scripts/add-jaynes-secrets.sh.
set -euo pipefail
VM=${JAYNES_VM:-jaynes-vm}
ZONE=${JAYNES_ZONE:-us-west1-b}
PROJECT=${JAYNES_PROJECT:-cyberiad-demo-gcp}
REPO_URL=https://github.com/techwiz42/jaynes.git
cd "$(dirname "$0")/.."

vm() { gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet --command "$1" 2> >(grep -v -i -E 'numpy|increase the performance|^$|^WARNING: $' >&2); }
health() { vm 'curl -s -m 5 http://127.0.0.1:8000/healthz' 2>/dev/null || true; }

ARG=origin/main
FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) ARG=$a ;;
  esac
done

if [ "$ARG" = "--status" ]; then
  vm 'echo "deployed: $(sudo -u jaynes git -C /opt/jaynes/app log --oneline -1 2>/dev/null || echo nothing)"; echo "service: $(systemctl is-active jaynes)"; echo "caddy: $(systemctl is-active caddy)"'
  echo "healthz: $(health)"
  exit 0
fi

restart() {
  vm 'sudo systemctl restart jaynes; for i in $(seq 1 40); do curl -s -m 2 http://127.0.0.1:8000/healthz | grep -q "\"ok\":true" && { echo "answering: $(sudo -u jaynes git -C /opt/jaynes/app log --oneline -1)"; exit 0; }; sleep 1; done; echo "the service did not answer in 40 seconds:"; sudo journalctl -u jaynes -n 15 --no-pager; exit 1'
}

if [ "$FORCE" = 0 ] && health | grep -q '"turns_in_progress":[1-9]'; then
  echo "a reply is being written right now; try again in a moment, or add --force"; exit 1
fi

if [ "$ARG" = "--restart" ]; then restart; exit $?; fi

git fetch -q origin
SHA=$(git rev-parse "$ARG^{commit}")
git branch -r --contains "$SHA" | grep -q . || { echo "$SHA is not pushed; the VM can only fetch pushed commits"; exit 1; }
echo "deploying ${SHA:0:8}"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git archive "$SHA" web | tar -x -C "$WORK"
(cd "$WORK/web" && npm ci --silent && npm run build --silent)

vm "sudo -u jaynes bash -s -- '$REPO_URL' '$SHA'" <<'REMOTE'
set -euo pipefail
REPO_URL=$1; SHA=$2
cd /opt/jaynes
[ -d app/.git ] || git clone -q "$REPO_URL" app
git -C app fetch -q origin
git -C app checkout -q --detach "$SHA"
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -e app
echo "code at $(git -C app log --oneline -1)"
REMOTE

COPYFILE_DISABLE=1 tar -C "$WORK/web" -cf - dist | vm 'sudo -u jaynes sh -c "rm -rf /opt/jaynes/app/web/dist && mkdir -p /opt/jaynes/app/web && tar -xf - -C /opt/jaynes/app/web"'
restart
