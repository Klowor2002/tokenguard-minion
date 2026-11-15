#!/usr/bin/env bash
# TokenGuard Minion — worked example: check USDT on BSC.
# Requires the provider API key for your configured OpenMinion agent in the
# environment (e.g. GEMINI_API_KEY for the gemini preset).
set -euo pipefail
cd "$(dirname "$0")/.."

export OPENMINION_HOME="$PWD"
export OPENMINION_DATA_ROOT="$PWD/.openminion"
# OpenMinion's HTTP client chokes on exotic no_proxy entries some systems set
# (e.g. "[::1]"); a plain localhost bypass is all local traffic needs.
export no_proxy="localhost,127.0.0.1"
export NO_PROXY="localhost,127.0.0.1"

.venv/bin/python -m tokenguard.cli bsc 0x55d398326f99059fF775485246999027B3197955 --auto-approve yes
