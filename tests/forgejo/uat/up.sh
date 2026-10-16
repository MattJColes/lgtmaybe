#!/usr/bin/env bash
# Start a local Forgejo with a registered runner for trying lgtmaybe.
#
#   ./up.sh          Forgejo and a runner with the `docker` label
#   ./up.sh --stub   also the stub model at http://stub:8080/v1 (no API key)
#
# Prints the accounts it created. Tear down with:
#   docker compose -f tests/forgejo/uat/compose.yml down -v
set -euo pipefail
cd "$(dirname "$0")"

profile=()
[[ "${1:-}" == "--stub" ]] && profile=(--profile stub)
compose=(docker compose -f compose.yml "${profile[@]}")
password="${UAT_PASSWORD:-lgtmaybe-uat-1}"

"${compose[@]}" up -d --wait forgejo

as_git() { "${compose[@]}" exec -T -u git forgejo forgejo "$@"; }
for user in maintainer outsider; do
  admin=()
  [[ "$user" == maintainer ]] && admin=(--admin)
  as_git admin user create "${admin[@]}" --username "$user" --password "$password" \
    --email "$user@example.com" --must-change-password=false >/dev/null 2>&1 || true
done

# Offline registration: Forgejo stores a runner keyed on a shared secret, and
# the runner's config carries the matching uuid and token.
mkdir -p .runner
if [[ ! -s .runner/config.yml ]]; then
  secret="$(openssl rand -hex 20)"
  uuid="$(as_git forgejo-cli actions register --name uat-runner --secret "$secret" | tr -d '\r\n')"
  cat > .runner/config.yml <<YAML
runner:
  capacity: 1
  labels:
    - docker:docker://data.forgejo.org/oci/node:22-bookworm
container:
  # Job containers join the compose network, so they reach http://forgejo:3000
  # and the stub by name.
  network: forgejo-uat
server:
  connections:
    uat:
      url: http://forgejo:3000/
      uuid: ${uuid}
      token: ${secret}
YAML
fi

"${compose[@]}" up -d

cat <<INFO

Forgejo is up at http://localhost:3000
  maintainer / ${password}   (admin, owns the test repositories)
  outsider   / ${password}   (no write access; use for fork PRs and slash commands)
Runner label: docker
INFO
if [[ ${#profile[@]} -gt 0 ]]; then
  echo "Stub model: INPUT_PROVIDER=openai-compatible INPUT_API_BASE=http://stub:8080/v1 INPUT_MODEL=mock"
fi
