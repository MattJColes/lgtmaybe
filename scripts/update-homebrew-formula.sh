#!/usr/bin/env bash
# Regenerate the Homebrew formula for lgtmaybe with up-to-date PyPI resources.
#
# litellm pulls a large transitive dependency tree, so a source-install formula
# needs every transitive dependency as a `resource` stanza. Hand-maintaining that
# is infeasible — this script regenerates the whole formula from the published
# PyPI release instead, so a dependency bump is picked up automatically. The CI
# job (.github/workflows/homebrew.yml) runs it on each release; a maintainer can
# also run it locally on a Mac to seed or verify the tap before the first release.
#
# Requires macOS with Homebrew installed (for `brew update-python-resources`) and
# python3 on PATH.
#
# Usage:
#   scripts/update-homebrew-formula.sh <version> <output-formula-path>
# e.g.
#   scripts/update-homebrew-formula.sh 0.7.2 ../homebrew-lgtmaybe/Formula/lgtmaybe.rb
set -euo pipefail

VERSION="${1:?usage: update-homebrew-formula.sh <version> <output-formula-path>}"
OUT="${2:?usage: update-homebrew-formula.sh <version> <output-formula-path>}"

# Strip any leading tag noise so callers can pass either "0.7.2" or "lgtmaybe-v0.7.2".
VERSION="${VERSION#lgtmaybe-v}"
VERSION="${VERSION#v}"

# 1. Resolve the sdist URL + sha256 for this version from PyPI. The release
#    workflow can fire before PyPI's trusted-publishing job has finished, so poll
#    until the version is available (up to ~10 minutes) rather than racing it.
pypi_json=""
for attempt in $(seq 1 60); do
  if pypi_json="$(curl -fsSL "https://pypi.org/pypi/lgtmaybe/${VERSION}/json" 2>/dev/null)"; then
    break
  fi
  echo "lgtmaybe ${VERSION} not on PyPI yet (attempt ${attempt}); waiting 10s…" >&2
  sleep 10
done
if [ -z "$pypi_json" ]; then
  echo "error: lgtmaybe ${VERSION} never appeared on PyPI" >&2
  exit 1
fi

read -r SDIST_URL SDIST_SHA AGE_SECONDS <<EOF
$(printf '%s' "$pypi_json" | python3 -c '
import json, sys
from datetime import datetime, timezone
data = json.load(sys.stdin)
sdist = next(u for u in data["urls"] if u["packagetype"] == "sdist")
uploaded = sdist.get("upload_time_iso_8601") or sdist.get("upload_time") or ""
try:
    dt = datetime.fromisoformat(uploaded.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = int((datetime.now(timezone.utc) - dt).total_seconds())
except ValueError:
    age = 10**9  # unknown upload time — assume old, let brew decide
print(sdist["url"], sdist["digests"]["sha256"], age)
')
EOF

echo "lgtmaybe ${VERSION}: ${SDIST_URL} (uploaded ${AGE_SECONDS}s ago)" >&2

# 2. Write the formula skeleton. `brew update-python-resources` fills in the
#    `resource` stanzas below the `depends_on` lines.
mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<RUBY
class Lgtmaybe < Formula
  include Language::Python::Virtualenv

  desc "Provider-agnostic pull request reviewer with keyless cloud auth"
  homepage "https://mattjcoles.github.io/lgtmaybe/"
  url "${SDIST_URL}"
  sha256 "${SDIST_SHA}"
  license "MIT"

  # The ast-grep-cli dependency ships a prebuilt binary via platform wheels, but
  # Homebrew installs resources from sdists (--no-binary), so we get the binary
  # from the core `ast-grep` formula instead and exclude ast-grep-cli from the
  # venv (see --exclude-packages below). lgtmaybe finds it on PATH via
  # shutil.which("ast-grep").
  depends_on "ast-grep"
  depends_on "python@3.12"
  # litellm pulls pydantic-core / tiktoken / tokenizers, whose sdists build with
  # a Rust toolchain. Needed at build time only — not a runtime dependency.
  depends_on "rust" => :build

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "Usage", shell_output("#{bin}/lgtmaybe --help")
  end
end
RUBY

# 3. Populate (or refresh) the resource stanzas for the full dependency tree.
#    ast-grep-cli is excluded: its sdist would try to build/fetch a Rust binary
#    inside Homebrew's network-sandboxed build and break `brew install`. The
#    `depends_on "ast-grep"` above supplies that binary instead.
#
#    Homebrew hardcodes a `--uploaded-prior-to = now - RELEASE_COOLDOWN_SECONDS`
#    (24h) pip cutoff to avoid resolving a freshly-compromised PyPI release. There
#    is no flag to disable it, so a version published less than ~24h ago cannot be
#    resolved yet. Distinguish that expected "too fresh" failure (exit 75, the
#    caller defers to a later scheduled run) from a genuine resolution failure
#    (propagate the real exit code so CI goes red).
COOLDOWN_WITH_MARGIN=$((25 * 3600))  # 24h cooldown + 1h margin for clock skew
if brew update-python-resources --exclude-packages=ast-grep-cli "$OUT"; then
  echo "Wrote ${OUT} for lgtmaybe ${VERSION}" >&2
else
  rc=$?
  if [ "$AGE_SECONDS" -lt "$COOLDOWN_WITH_MARGIN" ]; then
    echo "note: lgtmaybe ${VERSION} was published ${AGE_SECONDS}s ago, within Homebrew's 24h PyPI cooldown; brew cannot resolve its resources yet. Deferring — a later run will publish it once it ages past the cooldown." >&2
    exit 75
  fi
  echo "error: brew update-python-resources failed for lgtmaybe ${VERSION} (exit ${rc}); the version is past the PyPI cooldown, so this is a real resolution failure." >&2
  exit "$rc"
fi
