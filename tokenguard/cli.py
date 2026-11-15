"""TokenGuard Minion CLI.

Usage:
    python -m tokenguard.cli bsc 0x55d398326f99059fF775485246999027B3197955
    python -m tokenguard.cli 56 0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82 --auto-approve yes

Writes, per run, into the output dir (default ./out, override with
TOKENGUARD_OUT_DIR): report.json and diagnostics.json always; the Markdown
brief only if the operator approves the publisher's save_brief tool call.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from openminion import AgentOutputValidationError, ProviderError, TurnTimeoutError

from .agent import run_check
from .tools import CHAINS


def _resolve_chain(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    chain = CHAINS.get(raw.lower())
    if chain is None:
        raise SystemExit(f"unknown chain {raw!r}; use one of {sorted(set(CHAINS))} or a chain id")
    return chain


def main() -> int:
    parser = argparse.ArgumentParser(description="TokenGuard Minion — OpenMinion token-safety checker")
    parser.add_argument("chain", help="chain name (bsc, ethereum, base, polygon, arbitrum) or numeric id")
    parser.add_argument("address", help="token contract address")
    parser.add_argument(
        "--auto-approve",
        choices=("ask", "yes", "no"),
        default="ask",
        help="how to answer the approval prompt for the save_brief write (default: ask)",
    )
    parser.add_argument("--agent", default=None, help="configured OpenMinion agent id (default: default profile)")
    args = parser.parse_args()

    chain_id = _resolve_chain(args.chain)
    address = args.address.strip()

    out_dir = Path(__import__("os").environ.get("TOKENGUARD_OUT_DIR", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir / f"{chain_id}-{address[2:12].lower()}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    # the gated save_brief tool writes into TOKENGUARD_OUT_DIR; point it at run_dir
    __import__("os").environ["TOKENGUARD_OUT_DIR"] = str(run_dir)

    try:
        outcome = run_check(chain_id, address, auto_approve=args.auto_approve, agent_id=args.agent)
    except AgentOutputValidationError as exc:
        print(f"invalid model output: {exc.raw_text}", file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(f"provider failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except TurnTimeoutError as exc:
        print(f"turn timed out: {exc}", file=sys.stderr)
        return 3

    report = outcome["report"]
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    (run_dir / "diagnostics.json").write_text(json.dumps(outcome["diagnostics"], indent=2, default=str))

    print(f"\n=== TokenGuard verdict: {report.verdict} (risk {report.risk_score}/100) ===")
    print(f"{report.token_name} ({report.token_symbol}) on chain {report.chain_id}")
    if report.red_flags:
        print("Red flags:")
        for flag in report.red_flags:
            print(f"  - {flag}")
    if report.positives:
        print("Positives:")
        for pos in report.positives:
            print(f"  - {pos}")
    print(f"Summary: {report.summary}")
    print(f"Brief saved: {outcome['brief_saved']} ({outcome['brief_path']})")
    print(f"Artifacts: {run_dir}/report.json, {run_dir}/diagnostics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
