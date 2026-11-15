# Examples — real artifacts from live runs (2026-11-15)

Every file here was produced by an actual end-to-end run of the shipped
code against live data (GoPlus / honeypot.is / DexScreener) with a
relay-served Gemini Flash model. The `path` fields in each
`*-diagnostics.json` record which recovery path produced the report —
together the three runs exercise three different paths:

| Files | Token | Verdict | Assessor path | Brief path |
|---|---|---|---|---|
| `usdt-bsc-*` | USDT on BSC | SAFE 5/100 | `formatter` | subagent (see note) |
| `cake-bsc-*` | CAKE on BSC | SAFE 20/100 | `harvest` | `local-fallback` |
| `wbnb-bsc-*` | WBNB on BSC | SAFE 3/100 | `harvest` | `subagent` |

Notes:

- The USDT run's own brief file is not included: in that early run the
  brief-writer returned a runtime block notice as its text (finding F4 in
  `SDK_FEEDBACK.md`) — the bug that motivated the brief sanity checks.
  Its `report.json` is the richest of the three.
- The CAKE and WBNB reports came through the deterministic harvester, so
  some display fields (token name) are empty where the model's drifted
  JSON did not carry them; the facts it did carry are in `key_facts`,
  `positives` and `summary`.
- A second USDT check later the same day cited the stored first verdict
  ("Consistent with prior TokenGuard check") — the `previous_verdict`
  memory tool working as designed. See `demo/TRANSCRIPT.md`.
