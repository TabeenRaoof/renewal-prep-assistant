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

**Scope:** prep acceleration + workflow status tracking + activity audit log. The tool
surfaces and organizes; a human decides and acts. We are NOT doing remarketing, quoting,
term roll-forward, or sending. Single line of business (BOP).

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
- `pipeline/dates.py` — deterministic. Renewal stage + premium trend are pure arithmetic,
  never the model's job. Thresholds: URGENT ≤45d, ACT_NOW ≤120d, HEADS_UP ≤150d, else
  FUTURE; UNKNOWN if no expiration. (`add_one_year()` is NOT implemented — `roll_term()`
  in lifecycle.py is therefore also non-functional. See backlog.)
- `pipeline/lifecycle.py` — workflow vocabulary (`STATUSES`, `TERMINAL`) and
  `log_activity()` (single choke point for all audit entries). `roll_term()` exists but
  is broken (depends on the missing `add_one_year()`). Nothing AI-generated enters this
  module.
- `pipeline/llm.py` — the provider seam (see constraints below).
- `pipeline/schema.py` — Pydantic data contract for `ExtractedProfile`, `RenewalInsight`,
  and `RenewalBrief`. `activity_log` and `renewal_status` are NOT Pydantic fields on
  `RenewalBrief` — they live in the raw JSON dict that the app reads/writes directly.
  `ActivityEntry` is not defined. (See backlog for formalizing this.)
- `app.py` — Streamlit UI: single-page dashboard + brief drill-down via session-state
  router.

## TWO-AXIS MODEL (do not conflate these)

`RenewalBrief` carries two orthogonal status concepts:

| Field | Type | Who sets it | Meaning |
|---|---|---|---|
| `renewal_stage` | computed | `dates.renewal_stage()` in code | **Time urgency**: how close is expiration? (URGENT / ACT_NOW / HEADS_UP / FUTURE / UNKNOWN) |
| `renewal_status` | manual | account manager via UI dropdown | **Process position**: where are we in the workflow? (Not Started → In Progress → Remarketing → Pending Client → Renewed / Lost) |

`renewal_stage` is recomputed whenever `expiration_date` changes on Approve & save.
`renewal_status` is only set by the account manager via the dropdown in the brief view.
Both are stored in the brief JSON and shown in the dashboard Status column. `roll_term()`
is NOT implemented — status is not automatically reset when Renewed/Lost is selected.

## Persistence layout

```
outputs/
  {client_id}_brief.json    <- current brief (record of truth, includes status + log)
data/
  clients/{client_id}/      <- source documents per client (PDF, email, etc.)
  ams_export.csv            <- AMS/CRM portfolio (seed + any uploaded clients appended)
```

Briefs are plain JSON dicts loaded and saved by `app.py`. They are NOT validated through
`RenewalBrief(**data)` on load (no backfill). Term history archiving is NOT implemented.

## Activity log

The brief JSON carries an `activity_log` key (a list of dicts, appended, never
overwritten). All writes go through `lifecycle.log_activity()` so every entry is stamped
with ISO timestamp + actor + kind + detail. Implemented kinds:

- `created` — client uploaded and extracted for the first time
- `status_change` — workflow status changed via the dropdown ("X → Y")
- `doc_added` — new documents filed into an existing client folder
- `approved` — brief reviewed, edited, and saved via Approve & save

The log renders in the brief view as a collapsible section (newest first). It is
append-only; closing or reloading the app never truncates it.

**Not implemented:** `note` (manual free-text entry), `reextract` (diff-review logging),
per-field AI-proposed vs. agent-final diffing on the initial extraction.

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
   `RenewalBrief` is never passed to Gemini, so it is exempt — but note that
   `activity_log` and `renewal_status` are not Pydantic fields at all; they exist only
   in the raw JSON dict that `app.py` writes directly.
5. **Money = integers (USD), dates = ISO strings.** The model normalizes; code parses.

## Live mode status

The live Gemini path has been tested via Streamlit Community Cloud. The `_gemini_response()`
function tries structured output (`response_schema`) first and falls back to plain JSON
mode automatically if the schema is rejected. Real errors (invalid key, rate limit, etc.)
are surfaced directly in the UI rather than swallowed into a generic crash.

**Mock fixtures are derived from ground truth** (`fixtures/gen_fixtures.py` reads the
roster in `data/gen_data.py`). Mock mode shows the *correct* answer by construction —
it validates the plumbing and UI, NOT extraction quality. Only a live run tells you
whether the prompts actually work on messy real-world input.

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

Regenerate after edits: `python data/gen_data.py` (this also regenerates fixtures).

## Backlog (roughly priority order)

1. Per-carrier extraction examples (real dec pages vary far more than the synthetic set).
2. Field-level confidence scores (not just a per-document flag).
3. **Audit trail — complete:** `note` (manual free-text log entry), `reextract` kind,
   per-field AI-proposed vs. agent-final diff on initial extraction.
4. **Formalize lifecycle schema:** add `ActivityEntry` to `schema.py`, add `activity_log`
   and `renewal_status` as proper Pydantic fields on `RenewalBrief` so briefs load/save
   through the model rather than raw dict reads.
5. **Term roll-forward:** implement `dates.add_one_year()`, fix `roll_term()`, wire a
   "Bind renewal" action that archives the current term to `outputs/history/`, advances
   dates +1yr, rolls premiums, and resets status to Not Started.
6. Real AMS integration with an explicit reconciliation rule (which source wins).
7. Multi-line accounts (BOP + commercial auto + workers' comp) — where most commercial
   clients actually sit; the schema is single-line today.
8. Feed archived prior-term brief into `synthesize` for year-over-year delta analysis.
9. Tailor `suggested_approach` suggestions by `renewal_status` context.
10. **Import bulk clients:** import from CSV or Google Sheets, create clients in bulk.
11. **Churn-intelligence loop (future):** structured drop-reason capture in the AMS first,
    then feed recurring churn drivers back as weights on the signals `synthesize` already
    extracts. Data discipline must come before the mining.

## Code style note

Greenfield, but written to be readable: verbose inline comments explaining *why*,
explicit loops over clever comprehensions, finance/insurance reasoning stated in the
prompts and comments. Keep that voice if you extend it. Improve error handling and
input validation freely; don't restructure for cleverness.
