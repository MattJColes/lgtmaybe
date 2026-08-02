"""Shared provider defaults."""

from __future__ import annotations

# Default ollama endpoint when none is supplied. Used by both the credential
# resolver (to fill AuthConfig.api_base) and the factory (to configure the
# litellm client) so the two never drift.
DEFAULT_OLLAMA_BASE = "http://localhost:11434"

# Per-request timeout (seconds) for a direct cloud provider — the factory's
# shortest provider default, and the adapter's last-resort fallback when a caller
# builds it outside the factory or passes no timeout at all. One definition so the
# fallback can't drift below the default: a fallback that silently undercuts the
# configured budget is how a reasoning model's review turns into "1 of 8 review
# calls failed" with zero findings.
CLOUD_TIMEOUT = 600

# Sent as the api_key for an `openai-compatible` endpoint when the user supplies
# none. Local servers (llama.cpp / LM Studio / vLLM) need no auth, but the OpenAI
# client litellm uses rejects an empty key — so we pass this harmless placeholder.
OPENAI_COMPATIBLE_PLACEHOLDER_KEY = "lgtmaybe-no-key"
