#!/usr/bin/env bash
# Regenerate the Homebrew formula for lgtmaybe.
#
# lgtmaybe's dependency tree (via litellm) includes Rust extension packages
# (tokenizers, hf-xet) whose sdists cannot be built inside Homebrew's sandboxed
# build. So instead of vendoring every dependency as a source `resource` stanza
# — the usual Homebrew-Python approach, which fails to build this tree — the
# formula installs lgtmaybe and its dependencies from upstream PyPI **wheels**
# into an isolated virtualenv. This is not Homebrew-core audit style, but it
# builds reliably for a personal tap and tracks new releases with no
# hand-maintained resource list. It also sidesteps the 24h PyPI cooldown that
# `brew update-python-resources` imposes, so a release can publish immediately.
#
# Requires python3 on PATH (and, to seed/verify locally, macOS with Homebrew).
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

# Resolve the sdist URL + sha256 for this version from PyPI. The formula still
# carries the sdist as its `url`/`sha256` (Homebrew requires a checksummed
# source); the install step pulls the dependency tree as wheels. The release
# workflow can fire before PyPI's trusted-publishing job has finished, so poll
# until the version is available rather than racing it.
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

# Write the formula. `${SDIST_URL}`/`${SDIST_SHA}` are expanded by bash here;
# `#{version}` is left literal for Ruby. The install builds an isolated venv and
# installs lgtmaybe (which pulls its dependency wheels from PyPI).
mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<RUBY
class Lgtmaybe < Formula
  include Language::Python::Virtualenv

  desc "Provider-agnostic pull request reviewer with keyless cloud auth"
  homepage "https://lgtmaybe.coles.codes/"
  url "${SDIST_URL}"
  sha256 "${SDIST_SHA}"
  license "MIT"

  # The dependency wheels ship prebuilt extension dylibs (e.g. jiter) whose
  # install names use @rpath and lack header padding, so Homebrew cannot rewrite
  # them to an absolute path ("Failed to fix install linkage"). Preserve the
  # @rpath ids — they resolve correctly from the venv's fixed location anyway.
  preserve_rpath

  depends_on "ast-grep"
  depends_on "python@3.12"

  def install
    # lgtmaybe's dependency tree includes Rust extensions (tokenizers, hf-xet,
    # and litellm >= 1.92, which ships only manylinux wheels) whose sdists
    # cannot build inside Homebrew's sandbox (no Cargo), so install lgtmaybe
    # and its dependencies from upstream PyPI wheels into an isolated
    # virtualenv. --prefer-binary makes pip back off to the newest version
    # that has a macOS-compatible wheel instead of grabbing a newer sdist it
    # would then fail to compile. The venv is created plainly so ensurepip
    # provides pip.
    system "python3.12", "-m", "venv", libexec
    system libexec/"bin/python", "-m", "pip", "install", "--prefer-binary",
           "lgtmaybe==#{version}"
    bin.install_symlink libexec/"bin/lgtmaybe"
  end

  test do
    assert_match "Usage", shell_output("#{bin}/lgtmaybe --help")
  end
end
RUBY

echo "Wrote ${OUT} for lgtmaybe ${VERSION}" >&2
