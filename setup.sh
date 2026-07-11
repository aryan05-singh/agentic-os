#!/usr/bin/env bash
# One-time setup: venv, dependencies, config.yaml, API key.
# After this, use ./run.sh every time you want to talk to your agent.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "== agentic-os setup =="

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "No python3 found. Install Python 3.10+ first: https://www.python.org/downloads/"
  exit 1
fi

version=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
major=$("$PYTHON" -c 'import sys; print(sys.version_info[0])')
minor=$("$PYTHON" -c 'import sys; print(sys.version_info[1])')
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 10 ]; }; then
  echo "Found Python $version, but agentic-os needs 3.10+."
  exit 1
fi
echo "Using $PYTHON ($version)"

if [ ! -d .venv ]; then
  echo "Creating virtual environment (.venv)..."
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f config.yaml ]; then
  echo "Creating config.yaml from the example..."
  cp config.example.yaml config.yaml
  echo "  -> edit config.yaml to set your agent's name and personality."
fi

if [ ! -f .env ] || ! grep -q '^ANTHROPIC_API_KEY=' .env 2>/dev/null; then
  echo
  echo "agentic-os needs an Anthropic API key (from https://console.anthropic.com/)."
  read -r -p "Paste your ANTHROPIC_API_KEY (or press Enter to skip for now): " key
  if [ -n "$key" ]; then
    printf 'ANTHROPIC_API_KEY=%s\n' "$key" >> .env
    echo "Saved to .env (not committed to git)."
  else
    echo "Skipped — set ANTHROPIC_API_KEY yourself before running, or re-run setup.sh."
  fi
fi

echo
read -r -p "Install browser automation too (playwright + chromium, ~300MB)? [y/N] " browser
if [[ "$browser" =~ ^[Yy]$ ]]; then
  playwright install chromium
fi

echo
echo "Setup done. Run the agent with:"
echo "  ./run.sh"
