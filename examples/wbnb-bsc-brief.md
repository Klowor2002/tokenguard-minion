# Verdict: SAFE — WBNB (0xbb4C…095c) on BNB Smart Chain

## Why
- **No material red flags.** Only note: 24h volume ($4.69M) is modest relative to FDV (~$931M), which is normal for a wrapping/routing asset rather than a speculative token.
- **Honeypot checks pass** across two independent sources (GoPlus and honeypot.is): `is_honeypot = false`, no related honeypot from same creator, buy/sell/transfer taxes all 0%, `cannot_buy = false`.
- **Contract hygiene clean:** open-source verified, not mintable, not a proxy, transfers not pausable, no active blacklist function.
- **Market structure healthy:** 8,879,452 holders (broad distribution, no whale-domination signal); deepest PancakeSwap pair liquidity $34,978,250.50, independently corroborated at $35.6M by honeypot.is.
- **Established infrastructure:** canonical WBNB used by PancakeSwap and virtually every BSC dApp as the routing asset.

## Key facts
| Field | Value |
|---|---|
| Chain | BNB Smart Chain (id 56) |
| Address | 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c |
| Verdict | SAFE |
| Risk score | 3 / 10 |
| Confidence | 0.95 |
| Holders | 8,879,452 |
| Deepest-pair liquidity | $34,978,250.50 |
| 24h volume | $4.69M |
| Checked at | 2026-11-15T13:39:44Z |

## What would change this verdict
- A contract upgrade introducing mint, pause, blacklist, or tax functions (currently none in effect).
- A sharp drop in deepest-pair liquidity or concentration of holdings into a few whales.
- Any future honeypot or simulation failure reported by GoPlus or honeypot.is.

*Sources: goplus, honeypot.is, dexscreener. Automated research, not financial advice.*