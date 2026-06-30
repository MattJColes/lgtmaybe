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

read -r SDIST_URL SDIST_SHA <<EOF
$(printf '%s' "$pypi_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
sdist = next(u for u in data["urls"] if u["packagetype"] == "sdist")
print(sdist["url"], sdist["digests"]["sha256"])
')
EOF

echo "lgtmaybe ${VERSION}: ${SDIST_URL}" >&2

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
brew update-python-resources --exclude-packages=ast-grep-cli "$OUT"

echo "Wrote ${OUT} for lgtmaybe ${VERSION}" >&2
