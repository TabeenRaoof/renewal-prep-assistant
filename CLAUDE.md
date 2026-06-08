# CLAUDE.md — engineering handoff

Context for continuing this build in Claude Code. The README and WRITEUP are written
for a reviewer; this file is the engineering truth: decisions, constraints, and what
still needs doing. Read this first.

## What this is

A renewal-prep and lifecycle tracking tool for a regional insurance agency. Messy BOP
documents (declaration page PDFs, client emails, an AMS/CRM export) → a structured,
**agent-editable Renewal Prep Brief** that persists as the per-client record of truth
through the full renewal lifecycle, plus a portfolio aging dashboard.

**Primary user (confirmed):** the agency's **account manager / CSR** — services the
book and preps renewals. NOT the producer (sells), NOT the insured (the agency's
client). Outputs are internal prep, never client-facing copy.

**Scope:** prep acceleration + renewal lifecycle tracking (status, term roll-forward,
activity audit log, document re-ingestion). The tool still surfaces; a human decides
and acts. We are NOT doing remarketing, quoting, or sending.
Single line of business (BOP).

## Architecture

```
files -> ingest -> extract (LLM call 1) -> synthesize (LLM call 2) -> compute -> RenewalBrief
```

- `pipeline/ingest.py` — PDF/TXT/CSV(/DOCX) → text. PDFs with <40 chars of extractable
  text (scanned/image) are NOT OCR'd locally; their bytes are passed to the model as a
  document part (native multimodal fallback).
- `pipeline/extract.py` — LLM call 1. Documents → `ExtractedProfile`. The prompt is the
  heavy lifter: normalize money→int, dates→ISO, and **flag conflicts/low-confidence
  fields rather than guessing**.
- `pipeline/synthesize.py` — LLM call 2. Signal → `RenewalInsight`: care points, open
  issues, **light coverage-gap opportunities**, agent suggestions, summary.
- `pipeline/dates.py` — deterministic. Renewal stage + premium trend + `add_one_year()`
  are pure arithmetic in code, never the model's job. Thresholds: URGENT ≤45d,
  ACT_NOW ≤120d, HEADS_UP ≤150d, else FUTURE; UNKNOWN if no expiration. (Rationale:
  agencies should start the renewal conversation ~120 days out — proactive retention.)
- `pipeline/lifecycle.py` — deterministic. Workflow vocabulary (`STATUSES`, `TERMINAL`),
  `log_activity()` (single choke point for audit entries), and `roll_term()` (advances
  dates/premium when a renewal is bound). Nothing AI-generated enters this module.
- `pipeline/llm.py` — the provider seam (see constraints below).
- `pipeline/schema.py` — Pydantic data contract. Includes `ActivityEntry` and the
  lifecycle fields on `RenewalBrief` (see TWO-AXIS MODEL below).
- `app.py` — Streamlit UI: three tabs — Renewal Brief, Add Docs / Re-extract, Portfolio
  Dashboard.

## TWO-AXIS MODEL (do not conflate these)

`RenewalBrief` carries two orthogonal status concepts:

| Field | Type | Who sets it | Meaning |
|---|---|---|---|
| `renewal_stage` | computed | `dates.renewal_stage()` in code | **Time urgency**: how close is expiration? (URGENT / ACT_NOW / HEADS_UP / FUTURE / UNKNOWN) |
| `renewal_status` | manual | account manager via UI | **Process position**: where are we in the workflow? (NOT_STARTED → IN_PROGRESS → REMARKETING → PENDING_CLIENT → RENEWED / LOST) |

`renewal_stage` is recomputed whenever `expiration_date` changes. `renewal_status` is
only set by the app based on human input. The RENEWED and LOST statuses are terminal for
the current term; `roll_term()` resets to NOT_STARTED for the new term.

## Persistence layout

```
outputs/
  {client_id}_brief.json       <- current brief (record of truth, includes status + log)
  history/
    {client_id}/
      {term_label}_brief.json  <- archived snapshot of the RENEWED term
  sources/
    {client_id}/
      *.pdf / *.txt / ...      <- persisted source documents for re-extract
```

Saved briefs are validated through `RenewalBrief(**data)` on every load so new fields
backfill automatically on briefs saved before they were added.

## Activity log

Every `RenewalBrief` carries an `activity_log: List[ActivityEntry]` (appended, never
overwritten). All writes go through `lifecycle.log_activity()` so every entry is stamped
with ISO timestamp + actor initials. Kinds: `note | status_change | doc_added |
reextract | renewed`.

This satisfies backlog #4 (AI-proposed vs. agent-final audit trail): the diff-review
step logs which fields were applied from a re-extract, and the approve-and-save step
logs status changes. Prior AI proposals are in the fixture JSON; agent final is in the
saved brief.

## Constraints that are easy to get wrong (do not "fix" these)

1. **SDK is `google-genai`** (`from google import genai`). The older
   `google-generativeai` is deprecated and will not work. Verified against current docs.
2. **Model is `gemini-2.5-flash`** — on the free tier. Flash-Lite is the higher-RPD
   fallback if you hit throttling.
3. **Structured output requires pairing** `response_mime_type="application/json"` WITH
   `response_schema=<PydanticModel>`. One without the other fails.
4. **No `Optional`/`Union` in the LLM-facing schema models** (`ExtractedProfile`,
   `RenewalInsight`). Gemini structured output is fragile with nullable/union types.
   Missing values use sentinels: `"NOT_FOUND"` for text, `0` for money, `[]` for lists.
   **`RenewalBrief` is exempt from this rule** — it is never passed to Gemini as a
   `response_schema`, so the lifecycle fields (`activity_log`, `renewal_status`, etc.)
   can use Python-idiomatic defaults.
5. **Money = integers (USD), dates = ISO strings.** The model normalizes; code parses.

## THE BIG CAVEAT — live path is unexecuted

This was built in a sandbox that **cannot reach Gemini's endpoint**, so the entire
project has only ever run in **mock mode**. The live Gemini code in `llm.py` is written
against current SDK docs but **has never actually executed**. Treat the first live run
as a real test, not a formality:

```bash
pip install google-genai
export GEMINI_API_KEY=...        # free tier OK
streamlit run app.py
```

Things most likely to need adjustment on first live run: the `.parsed` vs `.text`
handling in `_gemini_response()`, nested-list schema acceptance (CarePoint /
Opportunity lists), and how scanned-PDF `Part.from_bytes` behaves with real bytes.

**Mock fixtures are derived from ground truth** (`fixtures/gen_fixtures.py` reads the
roster in `data/gen_data.py`). So mock mode shows the *correct* answer by construction —
it validates the plumbing and UI, NOT extraction quality. Only a live run tells you
whether the prompts actually work on messy input.

**Re-extract in mock mode returns the same fixture**, so the diff UI will always show
"no differences" in mock mode by design. This is honest behavior, not a bug. Real diffs
only appear under live Gemini or if a fixture file is hand-edited.

## Light upsell — design intent (keep it conservative)

`coverage_opportunities` surfaces ONLY real adequacy gaps in the *existing* policy, with
evidence. It must NOT manufacture needs, must NOT push unrelated products, and must stay
empty when timing is wrong (e.g. an angry client mid-complaint). The fixtures encode this:
CL-2001 and CL-2007 have a genuine gap; CL-2002 (frustrated) and CL-2017 (angry churn
risk) deliberately return none. If you extend this, preserve the restraint — an account
manager pushing unneeded coverage destroys the trust the tool exists to protect.

## Test data + traps (what to verify against)

18 synthetic clients in `data/clients/`. Key traps:
- **CL-2003** scanned image-only PDF (0 extractable chars) → native-model fallback.
- **CL-2007** email-vs-dec-page conflict (2nd location, unchanged limit) → must flag, not resolve.
- **CL-2015** no dec page → partial extraction, expiration UNKNOWN.
- **CL-2010** CRM premium typo'd low ($1,800 vs true $7,800) → reconciliation catch.
- Five date formats, four premium notations, blank fields, some clients with no email.

Regenerate after edits: `python data/gen_data.py && python fixtures/gen_fixtures.py`.

Deterministic lifecycle tests: `python tests/test_lifecycle.py`

## Backlog (roughly priority order)

1. **Live-test Gemini** on all 18 clients; compare against fixtures; tune prompts.
2. Per-carrier extraction examples (real dec pages vary far more than the synthetic set).
3. Field-level confidence scores (not just a per-document flag).
4. ~~Edit audit trail~~ — **DONE**: activity log + re-extract diff review satisfies this.
5. Real AMS integration with an explicit reconciliation rule (which source wins).
6. Multi-line accounts (BOP + commercial auto + workers' comp) — where most commercial
   clients actually sit; the schema is single-line today.
7. Feed archived prior-term brief into `synthesize` for year-over-year delta analysis
   (premium jump, dropped endorsements, coverage changes).
8. Tailor `suggested_approach` suggestions by `renewal_status` context (REMARKETING
   coaching differs from PENDING_CLIENT coaching).

9. **Import bulk clients (future):** add an import data from csv or sheets and create bulk clients based on that.

10. **Churn-intelligence loop (future):** the synthesis already extracts churn-risk
   signals (billing disputes, complaints, price pushback, intent to leave). Next step
   is structured drop-reason capture in the AMS, then feeding recurring churn drivers
   back as weights on those signals in active renewals. Data discipline must come
   before the mining — don't analyze churned conversations without structured capture
   first or you get confident-sounding but causally unreliable output.

## Code style note

Greenfield, but written to be readable: verbose inline comments explaining *why*,
explicit loops over clever comprehensions, finance/insurance reasoning stated in the
prompts and comments. Keep that voice if you extend it. Improve error handling and
input validation freely; don't restructure for cleverness.
