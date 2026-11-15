"""Offline tests for TokenGuard's API payload parsers.

Fixtures mirror the real response shapes of the GoPlus Security API,
honeypot.is and DexScreener (captured 2026-11-15, values trimmed). No
network is used. Run:  python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# tools.py imports the openminion package for the @tool decorator; the parsers
# themselves are pure. If openminion is not installed the import fails, which
# is correct: the parsers ship with the agent.
from tokenguard.tools import parse_dexscreener, parse_goplus, parse_honeypot

USDT_BSC = "0x55d398326f99059ff775485246999027b3197955"

GOPLUS_SAFE = {
    "code": 1,
    "result": {
        USDT_BSC.lower(): {
            "token_name": "Tether USD",
            "token_symbol": "USDT",
            "is_honeypot": "0",
            "honeypot_with_same_creator": "0",
            "buy_tax": "0",
            "sell_tax": "0",
            "is_open_source": "1",
            "is_mintable": "0",
            "is_proxy": "0",
            "is_blacklisted": "0",
            "transfer_pausable": "0",
            "cannot_buy": "0",
            "cannot_sell_all": "0",
            "is_anti_whale": "0",
            "holder_count": "8900000",
            "total_supply": "10000000000",
            "owner_address": "0x0000000000000000000000000000000000000000",
            "creator_address": "0x8894e0a0c962cb723c1976a4421c95949be2d4e3",
        }
    },
}

GOPLUS_HONEYPOT = {
    "code": 1,
    "result": {
        "0xdead00000000000000000000000000000000beef": {
            "token_name": "MoonSafeWin",
            "token_symbol": "MSW",
            "is_honeypot": "1",
            "buy_tax": "0.05",
            "sell_tax": "1",
            "is_open_source": "0",
            "is_mintable": "1",
            "holder_count": "212",
        }
    },
}

HONEYPOT_OK = {
    "token": {"name": "Tether USD", "symbol": "USDT", "decimals": 18, "totalSupply": 10000000000},
    "honeypotResult": {"isHoneypot": False},
    "simulationResult": {"buyTax": 0, "sellTax": 0, "transferTax": 0, "buyGas": 120000, "sellGas": 90000},
    "pair": {"pair": {"name": "USDT/WBNB"}, "liquidity": 152000000},
}

DEX_PAYLOAD = {
    "pairs": [
        {"chainId": "ethereum", "dexId": "uniswap", "pairAddress": "0xaaa",
         "priceUsd": "1.00", "liquidity": {"usd": 5000000}, "volume": {"h24": 900000},
         "fdv": 9000000000, "pairCreatedAt": 1600000000000},
        {"chainId": "bsc", "dexId": "pancakeswap", "pairAddress": "0xbbb",
         "priceUsd": "1.00", "liquidity": {"usd": 210000000}, "volume": {"h24": 55000000},
         "fdv": 9700000000, "pairCreatedAt": 1610000000000},
    ]
}


class TestGoPlus(unittest.TestCase):
    def test_safe_token(self):
        out = parse_goplus(GOPLUS_SAFE, USDT_BSC)
        self.assertTrue(out["found"])
        self.assertFalse(out["is_honeypot"])
        self.assertEqual(out["sell_tax_pct"], 0.0)
        self.assertTrue(out["is_open_source"])
        self.assertEqual(out["holder_count"], 8900000.0)
        self.assertIn("retrieved_at", out)

    def test_honeypot_token(self):
        out = parse_goplus(GOPLUS_HONEYPOT, "0xdead00000000000000000000000000000000beef")
        self.assertTrue(out["is_honeypot"])
        self.assertEqual(out["sell_tax_pct"], 1.0)
        self.assertTrue(out["is_mintable"])
        self.assertFalse(out["is_open_source"])

    def test_missing_token(self):
        out = parse_goplus({"code": 1, "result": {}}, USDT_BSC)
        self.assertFalse(out["found"])


class TestHoneypotIs(unittest.TestCase):
    def test_ok(self):
        out = parse_honeypot(HONEYPOT_OK)
        self.assertTrue(out["found"])
        self.assertFalse(out["is_honeypot"])
        self.assertEqual(out["pair_liquidity_usd"], 152000000)

    def test_empty(self):
        self.assertFalse(parse_honeypot({})["found"])


class TestDexScreener(unittest.TestCase):
    def test_prefers_requested_chain(self):
        out = parse_dexscreener(DEX_PAYLOAD, 56)
        self.assertTrue(out["found"])
        self.assertEqual(out["pair_address"], "0xbbb")
        self.assertEqual(out["liquidity_usd"], 210000000.0)

    def test_no_pairs(self):
        self.assertFalse(parse_dexscreener({"pairs": None}, 56)["found"])


class TestReportRecovery(unittest.TestCase):
    """The assessor-recovery path: models drift to the tool outputs' field
    names (captured live 2026-11-15 on a CAKE check). The local extractor
    must normalize that shape into a valid TokenSafetyReport."""

    DRIFTED = (
        'openminion: {\n  "chain_id": 56,\n'
        '  "contract_address": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",\n'
        '  "token_name": "CAKE (PancakeSwap Token)",\n  "verdict": "safe",\n'
        '  "is_honeypot": false, "buy_tax_pct": 0.0, "sell_tax_pct": 0.0,\n'
        '  "is_open_source": true, "is_mintable": true,\n'
        '  "pair_liquidity_usd": 13700000, "holder_count": 1900000,\n'
        '  "risk_flags": ["Token is mintable"],\n'
        '  "summary": "CAKE is the native token of PancakeSwap."\n}'
    )

    def test_drifted_shape_recovers(self):
        from tokenguard.agent import _extract_report

        report = _extract_report(self.DRIFTED)
        self.assertIsNotNone(report)
        self.assertEqual(report.verdict, "SAFE")
        self.assertEqual(report.risk_score, 10)  # band score for SAFE
        self.assertEqual(report.red_flags, ["Token is mintable"])
        self.assertEqual(report.key_facts["holder_count"], "1900000")
        self.assertEqual(report.key_facts["is_honeypot"], "False")

    def test_harvest_recovers_renamed_schema(self):
        # Second drift shape captured live: risk_verdict / honeypot /
        # holders_count_approx naming, no risk_score, no "verdict" key.
        from tokenguard.agent import _harvest_report

        raw = (
            'openminion: {"chain_id": 56, "token_name": "PancakeSwap Token (CAKE)", '
            '"risk_verdict": "LOW RISK", "honeypot": false, "buy_tax_pct": 0, '
            '"holders_count_approx": 1900000, '
            '"recommendation": "Safe to interact with."}'
        )
        report = _harvest_report(raw, 56, "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82")
        self.assertIsNotNone(report)
        self.assertEqual(report.verdict, "SAFE")
        self.assertEqual(report.risk_score, 10)
        self.assertEqual(report.key_facts["holders_count_approx"], "1900000")
        self.assertEqual(report.summary, "Safe to interact with.")

    def test_harvest_recovers_prose_verdict(self):
        # Pure-prose reply captured live (USDT re-check). The headline line
        # says SAFE; a later line contains the trap phrase "Verdict: DANGER
        # conditions not met", which must NOT win.
        from tokenguard.agent import _harvest_report

        raw = (
            "openminion: Safety verdict for 0x55d398326f99059fF775485246999027B3197955 "
            "(BSC, chain id 56 — USDt/Tether on BSC): SAFE with caveats.\n\n"
            "Risk score: ~10/100 (lowest band). Verdict: DANGER conditions not met — "
            "no honeypot signal from either source.\n\n"
            "Caveat: this is the official Binance-Peg USDt contract; residual risks "
            "are bridge/peg-related."
        )
        report = _harvest_report(raw, 56, "0x55d398326f99059fF775485246999027B3197955")
        self.assertIsNotNone(report)
        self.assertEqual(report.verdict, "SAFE")
        self.assertEqual(report.risk_score, 10)
        self.assertTrue(report.red_flags and "Binance-Peg" in report.red_flags[0])

    def test_prose_without_json_returns_none(self):
        from tokenguard.agent import _extract_report

        self.assertIsNone(_extract_report("A perfectly safe token, trust me."))

    def test_facts_prose_flattens_json(self):
        from tokenguard.agent import _render_facts_prose

        prose = _render_facts_prose(self.DRIFTED)
        self.assertIn("verdict: safe", prose)
        self.assertNotIn('"verdict"', prose)


if __name__ == "__main__":
    unittest.main()
