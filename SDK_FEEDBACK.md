# OpenMinion SDK — field feedback from building TokenGuard Minion

Tested with **openminion 0.1.21** (PyPI) on Python 3.12, 2026-11-15.
Every finding below was reproduced live during this build; the workarounds
we shipped are in `tokenguard/agent.py` and are noted per finding.

## What worked well first (genuinely)

- The typed error taxonomy (`ProviderError` with a `.code`, `TurnTimeoutError`,
  `AgentOutputValidationError` with `.raw_text`) made resilience code trivial
  to write: retry transients, recover validation failures, forensically log
  raw replies. This is the best-designed part of the SDK surface.
- `approval_callback` + `forced_tools` + `@tool(dangerous=True)` worked
  exactly as documented — the approval gate is a real gate, not decoration.
- `subagent()` delegation, session ids, and per-run diagnostics
  (run_id / stats / token counts) are clean and inspectable.
- `openminion doctor` gave a trustworthy baseline (0 critical) before runs.

## Findings

### F1 — The freshness verifier cannot see inside string tool results
When a run is classified freshness-sensitive with an exact-date obligation,
the verifier scans tool outputs recursively for dated-evidence keys
(`retrieved_at`, `published_at`, …). A tool that returns a JSON **string**
containing `"retrieved_at"` fails the check — the scan only descends into
dicts/lists — and the run's answer is replaced by a refusal
("Missing exact-date evidence for an exact-date freshness answer") that
mentions neither the cause nor the fix.
**Workaround shipped:** all tools return dicts, never JSON strings.
**Suggestion:** JSON-parse string tool results before scanning, or document
"return structured dicts" in the tool-authoring guide.

### F2 — The `task_update` entry envelope fails silently on weaker models
The first tool call of a task must carry a valid `task_update` object
(operation, `expected_revision`, a verbatim `user_quote`, objective).
Strong models (Gemini Flash class) produce it; smaller models
(Gemini Flash-Lite, Llama-3.3-70B) do not, and the failure surfaces as a
clarify-style reply — *"I could not safely update the task conditions"* —
which then fails `output_type` validation downstream, masking the real
cause two stages later.
**Suggestion:** surface the `reason_code` (`invalid_task_context_update`)
on the run result / exception so callers can distinguish "model can't do
the envelope" from "model answered badly".

### F3 — `output_type` coercion swallows the informative error
When a reply wraps JSON in prose, the coercion fallback extracts the first
decodable JSON object and validates it — but if *that* validation fails,
the original (uninformative) full-text error is re-raised and the
field-level error is discarded. In our case the model emitted numeric
values into a `dict[str, str]` field; finding that out took a forensic
detour through the runtime's session database.
**Suggestion:** include the extracted-object validation error in
`AgentOutputValidationError` (e.g. a `.stripped_error` attribute).

### F4 — A tool-less subagent can "succeed" with a runtime block notice
A `subagent()` with no tools, on a relay-served model, returned a
*completed* result whose text was a runtime notice:
`[system: UNEXECUTABLE_TOOL_ENVELOPE] The model generated a tool envelope
that could not be executed…`. Nothing in the result flags this as a
failure, so the notice flowed downstream and was nearly published as the
agent's deliverable.
**Workaround shipped:** content sanity checks on stage outputs
(`_looks_like_junk`), one retry, then a deterministic local fallback.
**Suggestion:** mark such results as failed (or expose a metadata flag) —
a block notice should never be indistinguishable from content.

### F5 — Free-tier provider limits collide with the runtime's prompt size
- Groq's on-demand free tier (8K TPM for gpt-oss-20b in our org) is below
  the runtime's base prompt: even a minimal no-tools "say ready" run fails
  with HTTP 413. WV629's triage entry reported the same wall.
- The Gemini preset's direct free key allows roughly 20 requests/day —
  a three-stage agent needs ~10 calls per run, so the default quickstart
  path exhausts itself in about two runs.
**Suggestion:** a `doctor`-level warning when the configured model's known
limits are below the runtime's base prompt estimate, and a docs note that
demo workloads need a higher-quota endpoint.

### F6 — No CLI path to switch an agent's model
`openminion agent` has no `update` subcommand; changing the model means
hand-editing `agents.json` in two places (`providers.<slot>.model` and
`agents.<id>.model_connections.<name>.models`), which must be kept in sync
manually. During this build we switched providers six times — the edit is
error-prone.
**Suggestion:** `openminion agent set-model <id> <model>` (and provider).

### F7 — Final replies can arrive truncated, with no finish signal
On relay-served models, final replies twice arrived cut mid-JSON
(~150 and ~1,200 chars). We could not tell whether the runtime's respond
budget or the relay truncated them; run diagnostics carry token counts but
no `finish_reason`.
**Suggestion:** record the provider's finish reason in run diagnostics.

### F9 — `forced_tools` does not guarantee the tool is called
A publisher agent with `forced_tools=["save_brief"]` completed a run with
`run_state: completed` and **tool_calls: 0** — the model replied with a
text confirmation and never invoked the tool. Nothing in the result flags
the omission; our pipeline only noticed because the expected file did not
exist.
**Workaround shipped:** verify the side effect, not the run state; when
the operator pre-approved the write (`--auto-approve yes`), perform it
locally and record the substitution in diagnostics.
**Suggestion:** a forced tool that is never called should fail the run (or
at least surface in diagnostics as an unmet requirement).

### F8 — Minor: lazy loading makes introspection misleading
`dir(openminion)` does not list lazily-loaded exports (e.g.
`TurnTimeoutError` imports fine but is invisible to `dir()`), which sends
developers to the source to confirm an export exists. A documented
`__all__` per release would help.

## Provider matrix observed (same agent code, config-only changes)

| Provider / model | Envelope | Structured output | Verdict |
|---|---|---|---|
| Gemini 3.6 Flash (direct key) | ✅ | ✅ (schema in instructions) | Works until the ~20 req/day free quota dies (429) |
| Gemini 3.7 Flash (direct key) | ✅ | — | ~120 s/call on the compat endpoint; runs time out |
| Gemini Flash-Lite (direct key) | ❌ task_update | — | Too weak for the envelope |
| Groq gpt-oss-20b / 120b | — | — | 413: free TPM below runtime prompt size |
| Llama 3.3 70B (Cloudflare Workers AI) | ❌ task_update | — | Clean tool calls, envelope fails |
| Qwen 3.8 Max (ApiNex relay) | ✅ | ❌ prose reports | Needs the recovery pipeline |
| DeepSeek V4 Pro (ApiNex relay) | ✅ | ❌ prose reports | Needs the recovery pipeline |
| Gemini 3.8 Flash (ApiNex relay) | ✅ | ⚠️ JSON wrapped in prose, sometimes truncated | Works with the recovery pipeline (our demo config) |

**Takeaway:** on the strongest first-party route the SDK works as
advertised. The gap is diagnosability: every failure above presented as a
generic refusal or a downstream validation error, and each took source
reading to attribute. Reason codes on results would close most of it.
