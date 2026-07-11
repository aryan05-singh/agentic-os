#!/usr/bin/env bash
# Run the agent — CLI chat or web dashboard. Run ./setup.sh first if you
# haven't already (creates the venv, installs deps, config.yaml, API key).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d .venv ]; then
  echo "No .venv found — run ./setup.sh first."
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "No API key found (checked .env and the environment)."
  echo "Run ./setup.sh to set one, or export ANTHROPIC_API_KEY yourself."
  exit 1
fi

if [ ! -f config.yaml ]; then
  echo "No config.yaml found — run ./setup.sh first."
  exit 1
fi

mode="${1:-}"
if [ -z "$mode" ]; then
  echo "How do you want to run agentic-os?"
  echo "  1) CLI chat (terminal)"
  echo "  2) Web dashboard (browser)"
  read -r -p "Choice [1/2]: " choice
  case "$choice" in
    2) mode="web" ;;
    *) mode="cli" ;;
  esac
fi

case "$mode" in
  cli)
    python -m agentic_os --config config.yaml
    ;;
  web)
    read -r -p "Port [8321]: " port
    port="${port:-8321}"
    python -m agentic_os.web --config config.yaml --port "$port"
    ;;
  *)
    echo "Usage: ./run.sh [cli|web]"
    exit 1
    ;;
esac
