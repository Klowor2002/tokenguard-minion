#!/usr/bin/env bash
# TokenGuard Minion — one-time setup.
# Creates a project-local venv + OpenMinion home so nothing touches your
# global Python or your default OpenMinion profile.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

export OPENMINION_HOME="$PWD"
export OPENMINION_DATA_ROOT="$PWD/.openminion"

if [ ! -f .openminion/agents.json ]; then
  echo "No OpenMinion agent configured yet. Create one with a provider you have a key for, e.g.:"
  echo "  export GEMINI_API_KEY=<your key>"
  echo "  .venv/bin/openminion setup --name tokenguard --provider gemini"
  echo "Then re-run this script to verify with doctor."
else
  .venv/bin/openminion doctor
  echo "Setup OK. Next: scripts/run_example.sh"
fi
