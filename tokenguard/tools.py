"""Read-only data tools for TokenGuard Minion.

Every tool wraps a free, keyless public API and returns a compact dict.
Returning structured dicts (not JSON strings) matters: the runtime's
freshness verifier scans tool-result outputs recursively for dated-evidence
keys such as ``retrieved_at``, and it cannot see inside a string.
Each payload carries ``retrieved_at`` (the OpenMinion runtime's freshness
verifier discounts tool evidence that has no recognisable date key) and a
``source`` tag. Network failures are returned as ``{"error": ...}`` payloads
so the agent can reason about missing data instead of crashing the run.

The ``parse_*`` helpers are pure functions so the parsing logic can be unit
tested offline against recorded payload shapes (see tests/).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from openminion import tool

CHAINS: dict[str, int] = {
    "ethereum": 1,
    "eth": 1,
    "bsc": 56,
    "bnb": 56,
    "polygon": 137,
    "matic": 137,
    "arbitrum": 42161,
    "arb": 42161,
    "base": 8453,
}

_TIMEOUT = 20
_HEADERS = {"User-Agent": "tokenguard-minion/0.1 (OpenMinion hackathon entry)"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------
# Pure parsers (offline-testable)
# --------------------------------------------------------------------------

def parse_goplus(payload: dict[str, Any], address: str) -> dict[str, Any]:
    """Reduce a GoPlus token_security payload to the facts the agent needs."""
    result = (payload.get("result") or {}).get(address.lower()) or {}
    if not result:
        return {"source": "goplus", "found": False, "retrieved_at": _now()}

    def flag(name: str) -> bool | None:
        raw = result.get(name)
        if raw is None:
            return None
        return str(raw) == "1"

    def num(name: str) -> float | None:
        raw = result.get(name)
        try:
            return float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None

    return {
        "source": "goplus",
        "found": True,
        "retrieved_at": _now(),
        "token_name": result.get("token_name"),
        "token_symbol": result.get("token_symbol"),
        "is_honeypot": flag("is_honeypot"),
        "honeypot_with_same_creator": flag("honeypot_with_same_creator"),
        "buy_tax_pct": num("buy_tax"),
        "sell_tax_pct": num("sell_tax"),
        "is_open_source": flag("is_open_source"),
        "is_mintable": flag("is_mintable"),
        "is_proxy": flag("is_proxy"),
        "is_blacklisted": flag("is_blacklisted"),
        "transfer_pausable": flag("transfer_pausable"),
        "cannot_buy": flag("cannot_buy"),
        "cannot_sell_all": flag("cannot_sell_all"),
        "is_anti_whale": flag("is_anti_whale"),
        "holder_count": num("holder_count"),
        "total_supply": result.get("total_supply"),
        "owner_address": result.get("owner_address"),
        "creator_address": result.get("creator_address"),
    }


def parse_honeypot(payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a honeypot.is IsHoneypot payload."""
    if not payload or "honeypotResult" not in payload:
        return {"source": "honeypot.is", "found": False, "retrieved_at": _now()}
    sim = payload.get("simulationResult") or {}
    pair = payload.get("pair") or {}
    token = payload.get("token") or {}
    return {
        "source": "honeypot.is",
        "found": True,
        "retrieved_at": _now(),
        "is_honeypot": bool((payload.get("honeypotResult") or {}).get("isHoneypot")),
        "buy_tax_pct": sim.get("buyTax"),
        "sell_tax_pct": sim.get("sellTax"),
        "transfer_tax_pct": sim.get("transferTax"),
        "pair_liquidity_usd": (pair.get("liquidity") if isinstance(pair, dict) else None),
        "token_name": token.get("name"),
        "token_symbol": token.get("symbol"),
    }


def parse_dexscreener(payload: dict[str, Any], chain_id: int, address: str = "") -> dict[str, Any]:
    """Pick the deepest-liquidity pair, preferring the requested chain and
    pairs where the token is the base token (quote-side pairs quote prices
    in the token's own units and report misleading price/FDV figures)."""
    pairs = payload.get("pairs") or []
    if not pairs:
        return {"source": "dexscreener", "found": False, "retrieved_at": _now()}

    def liq(pair: dict[str, Any]) -> float:
        return float((pair.get("liquidity") or {}).get("usd") or 0)

    chain_names = {1: "ethereum", 56: "bsc", 137: "polygon", 42161: "arbitrum", 8453: "base"}
    same_chain = [p for p in pairs if p.get("chainId") == chain_names.get(chain_id)]
    pool = same_chain or pairs
    if address:
        base_side = [
            p for p in pool
            if str((p.get("baseToken") or {}).get("address", "")).lower() == address.lower()
        ]
        if base_side:
            pool = base_side
    best = max(pool, key=liq)
    return {
        "source": "dexscreener",
        "found": True,
        "retrieved_at": _now(),
        "dex": best.get("dexId"),
        "pair_chain": best.get("chainId"),
        "pair_address": best.get("pairAddress"),
        "price_usd": best.get("priceUsd"),
        "liquidity_usd": liq(best),
        "volume_24h_usd": (best.get("volume") or {}).get("h24"),
        "fdv_usd": best.get("fdv"),
        "pair_created_at_ms": best.get("pairCreatedAt"),
        "pairs_seen": len(pairs),
    }


# --------------------------------------------------------------------------
# OpenMinion tools
# --------------------------------------------------------------------------

@tool(min_scope="READ_ONLY", idempotent=True)
def goplus_security(chain_id: int, contract_address: str) -> dict:
    """Fetch contract-security facts (honeypot flag, taxes, mintable, open
    source, holder count) for a token from the GoPlus Security API."""
    try:
        payload = _get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": contract_address.lower()},
        )
        return parse_goplus(payload, contract_address)
    except Exception as exc:  # surfaced to the model as data, not a crash
        return {"source": "goplus", "found": False, "error": str(exc), "retrieved_at": _now()}


@tool(min_scope="READ_ONLY", idempotent=True)
def honeypot_simulation(contract_address: str) -> dict:
    """Simulate a buy/sell of the token via honeypot.is to detect honeypots
    and measure buy/sell/transfer taxes."""
    try:
        payload = _get("https://api.honeypot.is/v2/IsHoneypot", params={"address": contract_address})
        return parse_honeypot(payload)
    except Exception as exc:
        return {"source": "honeypot.is", "found": False, "error": str(exc), "retrieved_at": _now()}


@tool(min_scope="READ_ONLY", idempotent=True)
def dex_market(contract_address: str, chain_id: int) -> dict:
    """Fetch DEX market facts (deepest pair liquidity, 24h volume, FDV, pair
    age) for a token from DexScreener."""
    try:
        payload = _get(f"https://api.dexscreener.com/latest/dex/tokens/{contract_address}")
        return parse_dexscreener(payload, chain_id, contract_address)
    except Exception as exc:
        return {"source": "dexscreener", "found": False, "error": str(exc), "retrieved_at": _now()}


def _state_path() -> Path:
    root = Path(os.environ.get("TOKENGUARD_STATE_DIR", "state"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "verdicts.json"


def load_previous(chain_id: int, contract_address: str) -> dict[str, Any]:
    """Plain helper (also wrapped as a tool) reading the local verdict store."""
    try:
        store = json.loads(_state_path().read_text())
    except Exception:
        store = {}
    entry = store.get(f"{chain_id}:{contract_address.lower()}")
    if not entry:
        return {"source": "tokenguard-memory", "found": False, "retrieved_at": _now()}
    return {"source": "tokenguard-memory", "found": True, "retrieved_at": _now(), "previous": entry}


def store_verdict(chain_id: int, contract_address: str, verdict: dict[str, Any]) -> None:
    """Persist a finished verdict so the next run can compare against it."""
    path = _state_path()
    try:
        store = json.loads(path.read_text())
    except Exception:
        store = {}
    store[f"{chain_id}:{contract_address.lower()}"] = {
        "verdict": verdict.get("verdict"),
        "risk_score": verdict.get("risk_score"),
        "checked_at": _now(),
    }
    path.write_text(json.dumps(store, indent=2))


@tool(min_scope="READ_ONLY", idempotent=True)
def previous_verdict(chain_id: int, contract_address: str) -> dict:
    """Look up whether TokenGuard has checked this token before, and what
    verdict it gave last time (explicit memory across runs)."""
    return load_previous(chain_id, contract_address)
