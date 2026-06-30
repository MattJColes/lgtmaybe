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
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md uv.lock ./
COPY src ./src

# Bundle every keyless-cloud extra so Bedrock (boto3), Vertex (google-auth) and
# Azure (azure-identity via OIDC) all work out of the box — litellm doesn't pull
# these itself, so without them a cloud review dies with a ModuleNotFoundError.
RUN uv sync --no-dev --frozen --extra azure --extra bedrock --extra vertex

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python", "-m", "lgtmaybe"]
