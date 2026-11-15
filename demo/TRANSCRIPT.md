# Demo transcript — TokenGuard Minion live runs (2026-11-15)

Real terminal output from live end-to-end runs on openminion 0.1.21
(provider: Gemini Flash via an OpenAI-compatible relay). A video version
of this demo is linked from the hackathon submission.

## Run 1 — USDT on BSC (first check)

```
$ python -m tokenguard.cli bsc 0x55d398326f99059fF775485246999027B3197955 --auto-approve yes
[stage assessor] raw reply kept (1199 chars) at state/last_assessor_raw.txt
[stage assessor] recovered report via formatter agent
[approval] auto-approved save_brief (call_5230689b046741af986a15fc)

=== TokenGuard verdict: SAFE (risk 5/100) ===
Tether USD (USDT) on chain 56
Red flags:
  - Contract is mintable (true) — standard for Tether-issued stablecoins,
    but means supply is under issuer control
Positives:
  - Not flagged as honeypot by GoPlus or honeypot.is
  - Buy tax 0%, sell tax 0%, transfer tax 0%
  - Open-source verified contract
  - Not blacklisted, not anti-whale, not proxy
  - Deep liquidity: ~$39.1M in deepest PancakeSwap pair ($42.2M per honeypot.is)
  - Mature pair: created May 2023, 30 pairs seen
  - Price on peg at $0.9992, FDV ~$9.18B
Summary: USDT (Tether USD) on BSC — legitimate, liquid major stablecoin;
no honeypot indicators.
Brief saved: True (out/56-55d398326f-…/brief-56-55d398326f.md)
```

What happened inside: the assessor called all four tools, then answered
with a truncated JSON draft — the recovery pipeline kept the raw reply,
and the formatter agent converted it into the validated report. The
publisher's `save_brief` write passed through the approval callback.

## Run 2 — USDT on BSC again (memory proof)

The assessor's own words on the re-check (raw reply, kept forensically):

> Safety verdict for 0x55d398326f99059fF775485246999027B3197955 (BSC,
> chain id 56 — USDt/Tether on BSC): SAFE with caveats.
> …
> - Previous verdict (TokenGuard memory): consistent prior assessment of
>   this address as non-honeypot / low risk.
> …
> Risk score: ~10/100 (lowest band).

The `previous_verdict` tool returned the stored Run-1 verdict and the
assessor cited it. The run completed end-to-end (formatter recovery;
brief-writer needed one retry after returning a runtime block notice —
the sanity check caught it).

## Run 3 — CAKE on BSC (schema-drift case)

The assessor produced a complete analysis in an invented schema
(`risk_verdict: "LOW RISK"`, `honeypot`, `holders_count_approx`, …).
Two runs died on this before the recovery pipeline grew its final layer;
with the shipped code the deterministic harvester maps that shape onto
the report schema (verdict SAFE, all scalar fields carried into
key_facts), the brief-writer's junk replies fall back to the local
renderer, and the run completes: **CAKE — SAFE**, 1,905,906 holders,
pair created 2023-04-01, mintable-under-governance flagged. WBNB
completed the same way (SAFE 3/100, harvest + subagent brief).
See `tests/test_parsing.py::TestReportRecovery` for these exact shapes
as regression fixtures, and `examples/` for the artifacts.

## A note on providers

A later recording attempt died at the formatter stage when the relay's
daily free-token allowance ran out (HTTP 402, resets 00:00 UTC) — the
retry logic burned its three attempts exactly as designed and the CLI
exited with code 1 and the provider's own message. Free-tier ceilings
are part of the terrain this agent is built for; the pipeline degrades
loudly, never silently.

## Offline tests

```
$ python -m unittest discover -s tests -v
...
Ran 12 tests in ~30s — OK
```
