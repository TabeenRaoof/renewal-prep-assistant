# Renewal Prep Assistant

An AI-powered renewal prep and lifecycle tracking tool for regional insurance agencies.
It turns the messy pile of documents behind a commercial **Business Owners Policy (BOP)**
renewal — declaration pages, client emails, an AMS/CRM export — into a structured,
**agent-editable Renewal Prep Brief** that persists as the per-client record of truth
through the full renewal lifecycle, and shows which accounts need attention.

Built for the agent, never the client: all outputs are internal prep. The tool does not
write or send anything to anyone.

---

## Quick start

```bash
pip install -r requirements.txt

# Generate the synthetic dataset + mock fixtures (one time)
python data/gen_data.py

# Run the app
streamlit run app.py
```

Opens in your browser. **No API key is required** — starts in mock mode and returns
saved sample outputs so you can click through the entire flow immediately.

### Live mode (real Gemini extraction)

```bash
pip install google-genai
export GEMINI_API_KEY=your_key_here   # free tier: https://aistudio.google.com
streamlit run app.py
```

With a key set, the two LLM calls hit `gemini-2.5-flash` for real extraction and
synthesis. Everything else is identical.

> Force mock mode even with a key: `export RENEWAL_MOCK=1`

---

## What it does

### Portfolio Dashboard (home screen)

The app opens on a **single-page dashboard** — your full book of business, aged by how
close each policy is to expiring:

- 🔴 **Urgent** — 45 days or less
- 🟠 **Act now** — within 120 days (the proactive window)
- 🟡 **Heads up** — within 150 days, on the radar

**Click a client's ID** to drill into their Renewal Brief. Other fields stay as plain
selectable text so you can copy from them without triggering navigation.

**➕ Add a new client** — expand the section at the top of the dashboard, drag in the
documents (dec page PDF, client emails), and click *Add client*. See [Duplicate
detection](#duplicate-detection) below for how the tool guards against creating a second
record for the same business.

### Renewal Brief

Clicking a client opens their brief — a full-page view with a **← Back to dashboard**
button at the top. The pipeline runs on open:

1. **Ingests** the files. Text PDFs are read locally; a scanned/image PDF is detected and
   sent to the model as a document (multimodal fallback).
2. **Extracts** policy + account facts into a typed schema (LLM call 1) — normalizing
   money to integers and the five date formats to ISO, and **flagging conflicts instead of
   silently guessing**.
3. **Synthesizes** retention signals — what the client cares about, open issues, **light
   coverage-gap opportunities** (real adequacy gaps in the existing policy, evidence-based,
   held back when timing is wrong, e.g. mid-complaint), and 2–4 suggestions *for the
   agent* (LLM call 2).
4. **Computes** days-to-expiration, renewal stage, and premium trend deterministically.

You then **edit every field**, set the **workflow status**, and **Approve & save** — the
human-in-the-loop step.

**Workflow status** (the manual process axis, distinct from time-urgency stage):
`Not Started` → `In Progress` → `Remarketing` → `Pending Client` → `Renewed` / `Lost`

The status is a dropdown in the brief — not free-text. Changes save immediately and are
visible in the portfolio dashboard Status column.

**Activity log**: every status change, document addition, and approval is stamped with
timestamp and actor. The log appears as a collapsible section in the brief view and is
append-only — it never overwrites prior entries.

---

## Duplicate detection

When you upload documents under **➕ Add a new client**, the tool reads them first before
writing anything, then checks the extracted name and file contents against every client
already in your book.

**Two independent signals are checked:**

| Signal | How it works |
|---|---|
| Business name | Normalized (case, punctuation, and legal-entity suffixes like `LLC / Inc / Corp` stripped), then matched **exactly** and **fuzzily** (≥ 84% similarity, plus containment — "Frosted Pine" flags against "Frosted Pine Bakery") |
| File content | SHA-256 hash of each uploaded file matched against files already on record — rename-proof, catches re-uploads even when the name failed to extract |

**Three outcomes:**

- **No match** — the tool creates a new client record immediately, no extra step.
- **Strong duplicate** (same business name, or a file already on record) — creating a
  second client is **blocked**. Business names are kept unique. The tool shows which
  existing client it matched and offers to file the new documents there instead.
- **Similar name** — a confirmation gate appears. Choose *Add to {existing client}* (if
  it's the same business) or *Create as new client* (if it's genuinely different).

**When documents are added to an existing client**, the tool files only what's new:
identical files (by content hash) are skipped, and a file with the same name but
different content is saved as `name (2).ext` so nothing is silently overwritten. The
client's saved brief is left untouched — your edits are preserved.

---

## Project structure

```
renewal-prep/
  app.py                  Streamlit UI — single-page dashboard + brief drill-down
  pipeline/
    schema.py             Pydantic data contract (ExtractedProfile, RenewalInsight, RenewalBrief)
    ingest.py             PDF/TXT/CSV(/DOCX) -> text + native-PDF parts
    llm.py                Provider seam: live Gemini  |  offline mock
    extract.py            LLM call 1 — documents -> structured facts (+ prompt)
    synthesize.py         LLM call 2 — signal -> priorities + suggestions (+ prompt)
    dates.py              Deterministic renewal staging and premium trend
    lifecycle.py          Workflow vocabulary and log_activity() audit helper
  data/
    gen_data.py           Generates 18 messy synthetic BOP clients + fixtures
    clients/<ID>/         Per-client dec.pdf + emails
    ams_export.csv        AMS/CRM export (one row per client)
  fixtures/
    gen_fixtures.py       Builds mock outputs from the roster ground truth
    <ID>.extract.json     Mock LLM output, call 1
    <ID>.synthesize.json  Mock LLM output, call 2
  outputs/                Approved briefs (per-client JSON, written on Approve & save)
    {id}_brief.json
```

---

## Scope

**Primary user:** the agency's **account manager / CSR** — the person who services the
book and preps renewals.

**In:** single line of business (BOP); PDF + TXT + CSV ingestion; structured extraction
with conflict flagging; retention synthesis; light coverage-gap detection (adequacy in
the existing policy, evidence-based, not sales); workflow status tracking with activity
audit log; human review/edit; portfolio dashboard with status column.

**Out (on purpose):** remarketing automation, client-facing messaging, quoting,
multi-line accounts, and OCR tuning. The tool accelerates prep and tracks the process;
it does not act.

---

## The mess this is tested against

The synthetic data is messy on purpose. Notable traps:

- **CL-2003 (Becker Roofing):** a scanned, image-only dec page — 0 extractable
  characters — forcing the native-PDF model fallback.
- **CL-2007 (Frosted Pine Bakery):** the email says a second location opened with more
  equipment, but the dec-page property limit is unchanged — a **conflict** the extractor
  flags rather than resolving.
- **CL-2015 (Bright Smiles Dental):** a prospect with **no dec page** — partial
  extraction, expiration UNKNOWN.
- **CL-2010 (Twin Cities Flooring):** the CRM premium is typo'd low ($1,800 vs the true
  $7,800 on the dec page) — a reconciliation catch.
- Five date formats, premiums written four different ways, blank fields, clients with no
  email (no retention signal to mine).


