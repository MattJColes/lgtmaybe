#!/usr/bin/env bash
# Stand up the local model servers the e2e suite (tests/e2e/) reviews against:
# ollama, llama.cpp, and vLLM — each serving a tiny qwen model so a full review
# finishes in seconds on CPU. Then run:
#
#     uv run pytest -m e2e
#
# Each backend the suite finds reachable runs; the rest auto-skip, so you can
# start only the ones you have (e.g. just ollama) and the others sit out.
#
# Usage:
#     scripts/e2e-up.sh            # start every backend that has a runner available
#     scripts/e2e-up.sh ollama     # start only the named backend(s)
#     scripts/e2e-up.sh down       # stop the docker-run backends (llama.cpp, vLLM)
#
# IMPORTANT — context window is a LAUNCH-TIME setting for llama.cpp/vLLM.
# Unlike ollama (where lgtmaybe passes `num_ctx` per request), the OpenAI-compatible
# servers fix their context at startup: llama.cpp `-c`, vLLM `--max-model-len`. The
# client cannot raise it later, so we bake a window here that comfortably fits the
# e2e fixture diff. Bump E2E_CTX if you point the suite at a bigger diff.
set -euo pipefail

# --- knobs (override via env) ----------------------------------------------
E2E_CTX="${E2E_CTX:-8192}"  # context window baked into llama.cpp/vLLM at launch

OLLAMA_MODEL="${LGTMAYBE_E2E_OLLAMA_MODEL:-qwen3:0.6b}"
OLLAMA_BASE="${LGTMAYBE_E2E_OLLAMA_BASE:-http://localhost:11434}"

# llama.cpp serves whatever GGUF it loads under any requested model id; this HF
# repo is a tiny qwen the server image can pull on its own.
LLAMACPP_PORT="${LLAMACPP_PORT:-8080}"
LLAMACPP_HF_REPO="${LLAMACPP_HF_REPO:-Qwen/Qwen2.5-0.5B-Instruct-GGUF}"
LLAMACPP_HF_FILE="${LLAMACPP_HF_FILE:-qwen2.5-0.5b-instruct-q4_k_m.gguf}"

# vLLM is strict: the served id must equal the --model it was launched with, and
# that id is what lgtmaybe must send (LGTMAYBE_E2E_VLLM_MODEL defaults to match).
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_MODEL="${LGTMAYBE_E2E_VLLM_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}"

LLAMACPP_CONTAINER="lgtmaybe-e2e-llamacpp"
VLLM_CONTAINER="lgtmaybe-e2e-vllm"

# --- helpers ----------------------------------------------------------------
log() { printf '\033[1;34m[e2e-up]\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

wait_for() {  # wait_for <url> <name>
  local url="$1" name="$2"
  for _ in $(seq 1 120); do
    if curl -sf "$url" >/dev/null 2>&1; then
      log "$name is up ($url)"
      return 0
    fi
    sleep 1
  done
  log "WARNING: $name did not become ready at $url"
  return 1
}

start_ollama() {
  if ! have ollama; then
    log "ollama not installed — skipping (install: curl -fsSL https://ollama.com/install.sh | sh)"
    return 0
  fi
  if ! curl -sf "$OLLAMA_BASE/api/tags" >/dev/null 2>&1; then
    log "starting ollama serve in the background"
    ollama serve >/tmp/lgtmaybe-e2e-ollama.log 2>&1 &
    wait_for "$OLLAMA_BASE/api/tags" "ollama" || return 0
  fi
  log "pulling $OLLAMA_MODEL"
  ollama pull "$OLLAMA_MODEL"
}

start_llamacpp() {
  if ! have docker; then
    log "docker not installed — skipping llama.cpp"
    return 0
  fi
  docker rm -f "$LLAMACPP_CONTAINER" >/dev/null 2>&1 || true
  log "starting llama.cpp server (ctx=$E2E_CTX) on :$LLAMACPP_PORT"
  # -c bakes the context window at launch — the client cannot raise it later.
  docker run -d --name "$LLAMACPP_CONTAINER" \
    -p "$LLAMACPP_PORT:8080" \
    ghcr.io/ggml-org/llama.cpp:server \
    -hf "$LLAMACPP_HF_REPO:$LLAMACPP_HF_FILE" \
    -c "$E2E_CTX" \
    --host 0.0.0.0 --port 8080 >/dev/null
  wait_for "http://localhost:$LLAMACPP_PORT/v1/models" "llama.cpp" || true
}

start_vllm() {
  if ! have docker; then
    log "docker not installed — skipping vLLM"
    return 0
  fi
  docker rm -f "$VLLM_CONTAINER" >/dev/null 2>&1 || true
  log "starting vLLM server (max-model-len=$E2E_CTX) on :$VLLM_PORT serving $VLLM_MODEL"
  # --max-model-len bakes the context window at launch (vLLM's equivalent of -c).
  docker run -d --name "$VLLM_CONTAINER" \
    -p "$VLLM_PORT:8000" \
    vllm/vllm-openai:latest \
    --model "$VLLM_MODEL" \
    --max-model-len "$E2E_CTX" >/dev/null
  wait_for "http://localhost:$VLLM_PORT/v1/models" "vLLM" || true
}

down() {
  log "stopping docker-run backends"
  docker rm -f "$LLAMACPP_CONTAINER" "$VLLM_CONTAINER" >/dev/null 2>&1 || true
  log "ollama (if started here) keeps running — stop it with: pkill -f 'ollama serve'"
}

# --- dispatch ---------------------------------------------------------------
targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(ollama llamacpp vllm)

for t in "${targets[@]}"; do
  case "$t" in
    ollama) start_ollama ;;
    llamacpp|llama.cpp) start_llamacpp ;;
    vllm) start_vllm ;;
    down|stop) down; exit 0 ;;
    *) log "unknown target: $t (expected: ollama, llamacpp, vllm, down)"; exit 2 ;;
  esac
done

log "ready — run the suite with:  uv run pytest -m e2e"
