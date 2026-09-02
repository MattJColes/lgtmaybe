# Container action image, published to GHCR and pulled by action.yml.
# Builds a lean runtime: deps resolved from the lockfile, the venv put on PATH,
# and the CLI invoked directly — no uv at runtime, so cold starts stay fast.
# Pinned by digest as well as tag: tags are mutable, so a digest pin makes the
# base image and the uv binary reproducible and tamper-evident (dependabot keeps
# the digests fresh alongside the tag).
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

COPY --from=ghcr.io/astral-sh/uv:0.10.6@sha256:2f2ccd27bbf953ec7a9e3153a4563705e41c852a5e1912b438fc44d88d6cb52c /uv /uvx /bin/

# git is needed at runtime to shallow-clone the PR's base branch for ast-grep
# cross-file symbol resolution (read-only — the base tree, never the PR head).
# ast-grep itself ships as a wheel via the ast-grep-cli dependency (uv sync below).
# curl additionally fetches the pinned gitleaks release below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# gitleaks is a Go binary with no PyPI wheel, so it cannot ride in the
# static-analysis extra like the rest. Pinned by version AND sha256 per arch:
# an unpinned fetch would make the image non-reproducible, and a wrong-arch
# binary is worse than a missing one — it passes the PATH lookup, fails at
# exec, and the runner's "a tool that fails is skipped" rule turns a broken
# secret scan into a silently clean one. Verifying the digest fails the build
# instead. Bump both digests together; there is no fallback by design.
ARG TARGETARCH=amd64
ARG GITLEAKS_VERSION=8.28.0
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) arch=x64;  sha=a65b5253807a68ac0cafa4414031fd740aeb55f54fb7e55f386acb52e6a840eb ;; \
      arm64) arch=arm64; sha=eff65261156100e5d94a6b3dec313d532fddfe19ae1590bf7a2b4f2699128356 ;; \
      *) echo "no pinned gitleaks build for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${arch}.tar.gz"; \
    curl -fsSL "${url}" -o /tmp/gitleaks.tgz; \
    echo "${sha}  /tmp/gitleaks.tgz" | sha256sum -c -; \
    tar -xzf /tmp/gitleaks.tgz -C /usr/local/bin gitleaks; \
    rm /tmp/gitleaks.tgz; \
    gitleaks version

# osv-scanner, likewise a Go binary with no wheel, pinned the same way.
ARG OSV_SCANNER_VERSION=2.0.2
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) sha=3abcfd7126c453a00421487e721b296e0cb68085bd431d6cef60872774170fc8 ;; \
      arm64) sha=5da413cb77ddb99bd115961e25ddf02490f6e1415abea1e15c9557057a457c08 ;; \
      *) echo "no pinned osv-scanner build for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_linux_${TARGETARCH}"; \
    curl -fsSL "${url}" -o /usr/local/bin/osv-scanner; \
    echo "${sha}  /usr/local/bin/osv-scanner" | sha256sum -c -; \
    chmod +x /usr/local/bin/osv-scanner; \
    osv-scanner --version

# Bake the OSV vulnerability database in at build time. The scanner runs inside
# the network-less sandbox, so it cannot fetch one itself — and with no database
# it reports nothing, which reads exactly like a clean bill of health. Fetching
# it here keeps the sandbox rule intact and bounds staleness to the release
# cadence. Per-ecosystem and modest (~30 MB each), so this is tens of megabytes,
# not the gigabyte a full mirror would cost.
ENV OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=/opt/osv-db
RUN set -eux; \
    mkdir -p /tmp/osv-seed /opt/osv-db; \
    : > /tmp/osv-seed/requirements.txt; \
    printf '{"dependencies":{}}' > /tmp/osv-seed/package.json; \
    printf 'module seed\n' > /tmp/osv-seed/go.mod; \
    cd /tmp/osv-seed; \
    osv-scanner scan source --offline-vulnerabilities --download-offline-databases \
        --no-resolve . > /dev/null 2>&1 || true; \
    rm -rf /tmp/osv-seed; \
    du -sh /opt/osv-db

WORKDIR /app
COPY pyproject.toml README.md uv.lock ./

# Bundle every keyless-cloud extra so Bedrock (boto3), Vertex (google-auth) and
# Azure (azure-identity via OIDC) all work out of the box — litellm doesn't pull
# these itself, so without them a cloud review dies with a ModuleNotFoundError.
#
# static-analysis carries the deterministic tool binaries. Without it nothing is
# on PATH, every tool is "skipped silently", and the `static_analysis` input is a
# no-op on the Action — the feature was container-invisible until this extra was
# added here.
RUN uv sync --no-dev --frozen --extra azure --extra bedrock --extra vertex \
    --extra static-analysis --no-install-project

COPY src ./src
RUN uv sync --no-dev --frozen --extra azure --extra bedrock --extra vertex \
    --extra static-analysis

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "-m", "lgtmaybe"]
