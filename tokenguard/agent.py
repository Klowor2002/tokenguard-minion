"""TokenGuard Minion — agent orchestration on the OpenMinion 0.1 SDK.

Pipeline (three OpenMinion stages, one workflow):

1. **assessor** — an ``Agent`` with four read-only tools and a pydantic
   ``output_type``. It gathers contract-security facts (GoPlus), a honeypot
   simulation (honeypot.is), DEX market facts (DexScreener) and the previous
   verdict from local memory, then returns a validated
   :class:`TokenSafetyReport`.
2. **brief-writer** — a bounded ``subagent()`` that turns the structured
   report into a plain-language Markdown brief.
3. **publisher** — an ``Agent`` whose only tool is the dangerous
   ``save_brief`` write, forced and routed through an ``approval_callback``:
   nothing is written to disk unless the operator approves.

Every stage's run id / session / stats are collected into a diagnostics
record so a run can be inspected after the fact.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from openminion import (
    Agent,
    AgentOutputValidationError,
    ProviderError,
    TurnTimeoutError,
    tool,
    subagent,
)
from pydantic import BaseModel, Field, ValidationError

from . import tools as tg_tools


class TokenSafetyReport(BaseModel):
    """Structured verdict produced by the assessor agent."""

    chain_id: int
    contract_address: str
    token_name: str = ""
    token_symbol: str = ""
    verdict: str = Field(description="One of SAFE, CAUTION, DANGER")
    risk_score: int = Field(description="0 (safest) to 100 (most dangerous)")
    red_flags: list[str] = []
    positives: list[str] = []
    key_facts: dict[str, str] = {}
    summary: str = ""
    sources_used: list[str] = []


_SCHEMA = json.dumps(TokenSafetyReport.model_json_schema(), indent=1)

ASSESSOR_INSTRUCTIONS = f"""OUTPUT CONTRACT (read first): your final reply must be exactly one
JSON object matching the schema at the end of these instructions. No
Markdown, no prose before or after, no code fences. All analysis goes
INSIDE the JSON fields (summary, red_flags, positives, key_facts).

You are TokenGuard, a token-safety assessor.

For the token you are given you MUST call all four tools before answering:
goplus_security, honeypot_simulation, dex_market, and previous_verdict.
Base every claim on tool output; if a tool returns an error or found=false,
say that source was unavailable and be more conservative.

Scoring guide (risk_score, 0 = safest, 100 = most dangerous):
- Either source says honeypot: verdict DANGER and risk_score >= 90.
- sell_tax >= 25%: +45. sell_tax 10-25%: +25. buy_tax >= 25%: +35, 10-25%: +15.
- Source code not open: +15. Mintable supply: +10. Upgradeable proxy: +5.
- Owner can blacklist or pause transfers: +10 each (max +20).
- Deepest-pair liquidity < $10,000: +20; $10k-$100k: +8; no DEX pair at all: +15.
- Holder count < 100: +10.
Verdict mapping: risk_score <= 24 -> SAFE, 25-59 -> CAUTION, >= 60 -> DANGER.
If the previous_verdict tool found an earlier check, mention in the summary
whether the verdict changed.

Answer with a single JSON object matching exactly this schema:
{_SCHEMA}
All key_facts values must be strings. Do not add fields not in the schema.
In particular, do NOT use tool-output field names (is_honeypot, buy_tax_pct,
risk_flags, recommendation, assessed_at) as reply fields — translate them
into the schema fields above (their facts belong in key_facts / red_flags).
Skeleton of a valid reply (placeholder values):
{{"chain_id": 56, "contract_address": "0x...", "token_name": "Example",
"token_symbol": "EX", "verdict": "CAUTION", "risk_score": 35,
"red_flags": ["..."], "positives": ["..."],
"key_facts": {{"sell_tax_pct": "0", "liquidity_usd": "123456"}},
"summary": "One-paragraph judgement.",
"sources_used": ["goplus", "honeypot.is", "dexscreener"]}}
Final reminder: reply with the JSON object only."""

BRIEF_INSTRUCTIONS = """You write short, plain-language Markdown briefs from
token-safety JSON reports. Structure: a one-line verdict header, "Why"
(bullets from red_flags and positives), "Key facts" (a small table), "What
would change this verdict", and a footer naming the data sources and noting
this is automated research, not financial advice. Keep it under 450 words.
Do not invent facts that are not in the JSON."""

PUBLISHER_INSTRUCTIONS = """You are a publishing tool-runner. Call save_brief
exactly once with the filename and content you are given, then reply with a
one-line confirmation."""

FORMATTER_INSTRUCTIONS = f"""You convert a token-safety analysis into a
structured record. The user message contains an analysis of one token,
possibly with a draft JSON block inside it. Extract ONLY the facts stated
in the analysis — never invent or re-judge anything — and reply with a
single JSON object matching exactly this schema:
{_SCHEMA}
All key_facts values must be strings (convert numbers to strings).
key_facts MUST carry every concrete fact stated in the analysis: taxes,
liquidity, holder count, price, pair age/count, honeypot flags.
sources_used MUST list every data source the analysis names
(e.g. goplus, honeypot.is, dexscreener). Do not add fields not in the schema."""


def _looks_like_junk(text: str) -> bool:
    """Detect runtime block notices and other non-content replies."""
    t = (text or "").strip()
    return (
        len(t) < 40
        or t.startswith("openminion: [system:")
        or "UNEXECUTABLE_TOOL_ENVELOPE" in t
        or "could not safely update the task conditions" in t
        or "couldn't provide a grounded current" in t
    )


def _render_brief_locally(report: TokenSafetyReport) -> str:
    """Deterministic Markdown brief rendered from the structured report.

    Fallback for when the brief-writer subagent's reply is unusable (some
    model/relay combinations mangle the tool envelope of a tool-less
    subagent and return a runtime block notice instead of prose).
    """
    lines = [
        f"# TokenGuard brief — {report.token_name or 'Unknown token'} "
        f"({report.token_symbol or '?'})",
        "",
        f"**Verdict: {report.verdict}** · risk score {report.risk_score}/100 · "
        f"chain {report.chain_id} · `{report.contract_address}`",
        "",
        "## Why",
    ]
    for flag in report.red_flags:
        lines.append(f"- ⚠️ {flag}")
    for pos in report.positives:
        lines.append(f"- ✅ {pos}")
    if report.key_facts:
        lines += ["", "## Key facts", "", "| Fact | Value |", "|---|---|"]
        for key, value in report.key_facts.items():
            lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## Summary",
        "",
        report.summary or "(no summary)",
        "",
        f"_Sources: {', '.join(report.sources_used) or 'see report.json'}. "
        "Automated research, not financial advice._",
    ]
    return "\n".join(lines)


def _dump_raw(stage: str, raw: str) -> None:
    """Keep the raw reply of a stage that failed validation, for forensics."""
    try:
        state_dir = Path(os.environ.get("TOKENGUARD_STATE_DIR", "state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"last_{stage}_raw.txt").write_text(raw)
        print(f"[stage {stage}] raw reply kept ({len(raw)} chars) at state/last_{stage}_raw.txt")
    except OSError:
        pass


def _normalize_candidate(obj: dict[str, Any]) -> dict[str, Any]:
    """Map a model's near-miss report object onto the TokenSafetyReport shape.

    Observed drift (live, 2026-11-15): models anchor on the *tool outputs'*
    field names and emit objects like {"verdict": "safe", "risk_flags": [...],
    "is_honeypot": false, "buy_tax_pct": 0.0, ...} with no risk_score. The
    mapping below is deterministic and conservative: it renames what is
    unambiguous, upper-cases the verdict, stringifies key_facts, and — only
    when the model omitted risk_score — derives a band score from the
    model's own verdict (SAFE 10 / CAUTION 40 / DANGER 80, honeypot 95).
    """
    obj = dict(obj)
    kf = obj.get("key_facts")
    if isinstance(kf, dict):
        for key in ("token_name", "token_symbol", "chain_id", "contract_address"):
            if key not in obj and key in kf:
                obj[key] = kf[key]
    if "red_flags" not in obj and isinstance(obj.get("risk_flags"), list):
        obj["red_flags"] = [str(x) for x in obj["risk_flags"]]
    verdict = str(obj.get("verdict", "")).strip().upper()
    if verdict in ("SAFE", "CAUTION", "DANGER"):
        obj["verdict"] = verdict
    score = obj.get("risk_score")
    if isinstance(score, float):
        obj["risk_score"] = round(score)
    elif not isinstance(score, int):
        base = {"SAFE": 10, "CAUTION": 40, "DANGER": 80}.get(verdict, 50)
        if obj.get("is_honeypot") is True:
            base = 95
        obj["risk_score"] = base
    if isinstance(obj.get("key_facts"), dict):
        obj["key_facts"] = {str(k): str(v) for k, v in obj["key_facts"].items()}
    else:
        facts = {
            k: obj[k]
            for k in (
                "is_honeypot", "buy_tax_pct", "sell_tax_pct", "transfer_tax_pct",
                "is_open_source", "is_mintable", "is_proxy", "is_blacklisted",
                "holder_count", "pair_liquidity_usd", "dexscreener_liquidity_usd",
                "volume_24h_usd", "price_usd", "fdv_usd",
            )
            if k in obj
        }
        if facts:
            obj["key_facts"] = {k: str(v) for k, v in facts.items()}
    if not obj.get("sources_used"):
        obj["sources_used"] = ["goplus", "honeypot.is", "dexscreener"]
    return obj


def _extract_report(text: str) -> TokenSafetyReport | None:
    """Recover a TokenSafetyReport from a reply that wraps JSON in prose.

    The SDK's own coercion scans for the first decodable JSON object; this
    local pass keeps scanning until an object actually *validates* (after
    deterministic normalization), so a stray brace or a draft block early
    in the prose cannot shadow the real report.
    """
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or "verdict" not in value:
            continue
        try:
            return TokenSafetyReport.model_validate(_normalize_candidate(value))
        except ValidationError:
            continue
    return None


def _render_facts_prose(raw: str) -> str:
    """Render a raw assessor reply as flat fact lines for the formatter.

    A model asked to convert JSON→JSON tends to echo the input schema
    verbatim (observed live). The same model converts *prose facts* into
    the target schema reliably, so when the raw reply contains a JSON
    object we flatten it to "key: value" lines first.
    """
    decoder = json.JSONDecoder()
    for start, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "verdict" in value:
            lines = []
            for key, val in value.items():
                if isinstance(val, (dict, list)):
                    val = json.dumps(val)
                lines.append(f"{key}: {val}")
            return "\n".join(lines)
    return raw


@tool(dangerous=True)
def save_brief(filename: str, content: str) -> str:
    """Write a Markdown brief to the output directory. This is a write, so
    OpenMinion routes it through the run's approval callback first."""
    out_dir = Path(os.environ.get("TOKENGUARD_OUT_DIR", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name  # never let a model escape the out dir
    path = out_dir / safe_name
    path.write_text(content)
    return f"wrote {path} ({len(content)} chars)"


def _diagnostics(stage: str, result: Any, started: float) -> dict[str, Any]:
    stats = getattr(result, "stats", None)
    if stats is not None and not isinstance(stats, (dict, list, str, int, float)):
        stats = getattr(stats, "model_dump", None) and stats.model_dump() or str(stats)
    return {
        "stage": stage,
        "run_id": getattr(result, "run_id", None),
        "session_id": getattr(result, "session_id", None),
        "run_state": str(getattr(result, "run_state", "")) or None,
        "wall_seconds": round(time.time() - started, 2),
        "stats": stats,
    }


def make_approval_callback(mode: str) -> Callable[[str, dict[str, Any], str], bool]:
    """Approval policy for the dangerous save_brief tool.

    mode: "yes" (approve, for scripted demos), "no" (deny), "ask" (prompt).
    """

    def decide(tool_name: str, args: dict[str, Any], approval_id: str) -> bool:
        if tool_name != "save_brief":
            print(f"[approval] denying unexpected tool {tool_name}")
            return False
        if mode == "yes":
            print(f"[approval] auto-approved {tool_name} ({approval_id})")
            return True
        if mode == "no":
            print(f"[approval] denied {tool_name} ({approval_id})")
            return False
        try:
            answer = input(f"[approval] allow {tool_name} -> {args.get('filename')!r}? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")

    return decide


def _run_stage(label: str, fn: Callable[[], Any], attempts: int = 3) -> Any:
    """Run one pipeline stage, retrying transient provider failures.

    Free-tier endpoints occasionally answer 503/rate-limit; the runtime
    surfaces those as ProviderError. A bounded retry with backoff keeps a
    demo run alive without hiding persistent failures.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (ProviderError, TurnTimeoutError) as exc:
            last = exc
            code = getattr(exc, "code", type(exc).__name__)
            print(f"[stage {label}] transient error (attempt {attempt}/{attempts}): {code}")
            if attempt < attempts:
                time.sleep(15 * attempt)
    raise last  # type: ignore[misc]


def run_check(
    chain_id: int,
    contract_address: str,
    *,
    auto_approve: str = "ask",
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Run the full TokenGuard workflow for one token.

    Returns {"report": TokenSafetyReport, "brief": str,
             "brief_saved": bool, "diagnostics": [ ... ]}.
    """
    diagnostics: list[dict[str, Any]] = []
    session_id = f"tokenguard-{chain_id}-{contract_address[2:12].lower()}"

    # -- stage 1: assessor -------------------------------------------------
    def _assess() -> Any:
        with Agent(
            name="tokenguard-assessor",
            agent_id=agent_id,
            instructions=ASSESSOR_INSTRUCTIONS,
            tools=[
                tg_tools.goplus_security,
                tg_tools.honeypot_simulation,
                tg_tools.dex_market,
                tg_tools.previous_verdict,
            ],
            output_type=TokenSafetyReport,
            session_id=session_id,
        ) as assessor:
            return assessor.run(
                f"Assess token {contract_address} on chain id {chain_id}.",
                timeout_seconds=600,
            )

    started = time.time()
    report: TokenSafetyReport | None = None
    try:
        result = _run_stage("assessor", _assess)
        entry = _diagnostics("assessor", result, started)
        entry["path"] = "direct"
        diagnostics.append(entry)
        report = result.output
    except AgentOutputValidationError as exc:
        # The model did the research but answered in prose (common with
        # OpenAI-compatible relays): recover the structured report from
        # the raw reply, or hand the reply to a tool-less formatter agent
        # whose only job is the schema conversion.
        raw = exc.raw_text or ""
        _dump_raw("assessor", raw)
        report = _extract_report(raw)
        if report is not None:
            diagnostics.append(
                {"stage": "assessor", "path": "extracted", "wall_seconds": round(time.time() - started, 2)}
            )
            print("[stage assessor] recovered report by local JSON extraction")
        else:
            def _format() -> Any:
                with Agent(
                    name="tokenguard-formatter",
                    agent_id=agent_id,
                    instructions=FORMATTER_INSTRUCTIONS,
                    output_type=TokenSafetyReport,
                ) as formatter:
                    return formatter.run(
                        f"Token under analysis: {contract_address} on chain id {chain_id}.\n"
                        f"Facts from the analysis:\n{_render_facts_prose(raw)}",
                        timeout_seconds=300,
                    )

            try:
                fmt_result = _run_stage("formatter", _format)
                entry = _diagnostics("assessor", fmt_result, started)
                entry["path"] = "formatter"
                diagnostics.append(entry)
                report = fmt_result.output
                print("[stage assessor] recovered report via formatter agent")
            except AgentOutputValidationError as fmt_exc:
                _dump_raw("formatter", fmt_exc.raw_text or "")
                report = _extract_report(fmt_exc.raw_text or "")
                if report is None:
                    raise
                diagnostics.append(
                    {"stage": "assessor", "path": "formatter-extracted",
                     "wall_seconds": round(time.time() - started, 2)}
                )
                print("[stage assessor] recovered report from formatter raw reply")
    tg_tools.store_verdict(chain_id, contract_address, report.model_dump())

    # -- stage 2: brief writer (delegated subagent) ------------------------
    def _write_brief() -> Any:
        with Agent(name="tokenguard-coordinator", agent_id=agent_id) as coordinator:
            writer = subagent(
                coordinator,
                name="brief-writer",
                instructions=BRIEF_INSTRUCTIONS,
                timeout_seconds=300,
            )
            return writer.run(
                "Write the brief for this token-safety report JSON:\n"
                + report.model_dump_json(indent=1)
            )

    started = time.time()
    brief_result = _run_stage("brief-writer", _write_brief)
    entry = _diagnostics("brief-writer", brief_result, started)
    brief = brief_result.text
    if _looks_like_junk(brief):
        print("[stage brief-writer] reply unusable, retrying once")
        brief_result = _run_stage("brief-writer", _write_brief)
        entry = _diagnostics("brief-writer", brief_result, started)
        brief = brief_result.text
    if _looks_like_junk(brief):
        print("[stage brief-writer] falling back to local brief renderer")
        entry["path"] = "local-fallback"
        brief = _render_brief_locally(report)
    else:
        entry["path"] = "subagent"
    diagnostics.append(entry)

    # -- stage 3: publisher (approval-gated write) --------------------------
    filename = f"brief-{chain_id}-{contract_address[2:12].lower()}.md"

    def _publish() -> Any:
        with Agent(
            name="tokenguard-publisher",
            agent_id=agent_id,
            instructions=PUBLISHER_INSTRUCTIONS,
            tools=[save_brief],
            forced_tools=["save_brief"],
        ) as publisher:
            return publisher.run(
                f"Save this brief with save_brief. filename: {filename}\n"
                f"content:\n{brief}",
                approval_callback=make_approval_callback(auto_approve),
                timeout_seconds=300,
            )

    started = time.time()
    pub_result = _run_stage("publisher", _publish)
    diagnostics.append(_diagnostics("publisher", pub_result, started))
    out_path = Path(os.environ.get("TOKENGUARD_OUT_DIR", "out")) / filename

    return {
        "report": report,
        "brief": brief,
        "brief_path": str(out_path) if out_path.exists() else None,
        "brief_saved": out_path.exists(),
        "diagnostics": diagnostics,
    }
