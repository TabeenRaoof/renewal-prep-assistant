# Renewal Prep Assistant — Write-up

## The workflow, and why this one

Regional insurance agencies run an annual **renewal treadmill**: every commercial
policy expires once a year, and the agency has to prepare each one — pull the current
coverage, remember what the client cares about, catch anything that changed — before
reaching out. The information needed is real but scattered: a declaration-page PDF
(sometimes a scan), a thread of client emails, and a row in an agency-management
system that someone half-filled in. Today an account manager reassembles this by hand,
per client, which is slow and easy to get wrong when a book runs to hundreds of policies.

I picked it because it is unglamorous, document-heavy, and judgement-laden — exactly
where extraction-plus-synthesis earns its keep — and because the failure mode of doing
it badly (a missed renewal, a coverage gap, an avoidable churn) is concrete and costly.

I scoped it to **renewal *prep* acceleration**, not remarketing automation. The tool
assembles the brief and surfaces what matters; a human still decides and acts. The
primary user is the agency's **account manager / CSR** — the person who services the
book — not the producer and not the insured. That boundary is deliberate: the
defensible value is structuring the mess, and an insurance agent is not going to let
an AI send things to clients unsupervised.

## What the tool does

For one client, it ingests the documents, then makes two LLM calls against
`gemini-2.5-flash`: one **extracts** policy and account facts into a typed schema, the
other **synthesizes** retention signals (what they care about, open issues, and
suggestions for the agent). It also does **light coverage-gap detection**: real
adequacy gaps in the *existing* policy (a limit that no longer matches a stated
business change, an endorsement clearly missing for the trade), surfaced only with
evidence and deliberately withheld when the timing is wrong — it stays silent on a
client mid-complaint. That keeps it service-minded, not a sales bot. Money is
normalized to integers, five date formats to ISO, and — the part that matters most on
real data — when two documents disagree, the extractor **flags the conflict and the
low-confidence field instead of silently guessing**. Renewal stage and premium trend
are then computed in plain code, not by the model, so they are deterministic and
trustworthy.

The agent **reviews and edits every field**, then approves. A second tab ages the whole
book from the AMS export and shows which accounts are in the proactive window. It runs
with no API key (mock mode) so it can be launched and clicked through with zero setup.

## What breaks at scale (the honest part)

- **Carrier-specific dec pages.** My synthetic pages vary, but real carriers use wildly
  different layouts. Extraction quality holds far better with per-carrier prompt examples
  or a few labeled samples per carrier than with one generic prompt.
- **Scanned-document quality.** The native-PDF fallback works, but fax-quality scans and
  handwriting degrade it. A real deployment needs an OCR confidence gate and a human queue
  for low-confidence reads.
- **Hallucinated or over-confident extractions.** The conflict-flagging and the mandatory
  human approval step are the guardrail. At volume you would also want field-level
  confidence scores and spot-check sampling, not just a per-document flag.
- **The AMS is the source of truth, not the documents.** Premiums, expirations, and loss
  runs really live in the agency-management system. The CSV reconciliation here hints at
  it (one premium is typo'd low on purpose); production needs a real AMS integration and a
  defined rule for which source wins when they disagree.
- **Audit trail (implemented for core events).** Every brief carries an append-only
  activity log stamped with timestamp and actor. The implemented kinds are: client
  created, status changed, documents added, and brief approved. Not yet implemented:
  manual free-text notes, re-extract diff logging, and per-field AI-proposed vs.
  agent-final versioning of the initial extraction.
- **Free-tier rate limits.** Flash is ~10 req/min / 250 req/day on the free tier — fine for
  a demo and a small book, not for a nightly run across thousands of policies without a paid
  tier (and a no-training data setting before any real client PII touches it).

## Lifecycle tracking (implemented)

The tool tracks where each renewal sits in the process alongside the time-urgency stage:

- **Workflow status:** the account manager advances each renewal through
  `Not Started → In Progress → Remarketing → Pending Client → Renewed / Lost` via a
  dropdown in the brief. Status changes are saved immediately and reflected in the
  dashboard Status column. This is intentionally distinct from the computed time-urgency
  stage (URGENT / ACT_NOW / …) — two axes that answer different questions.
- **Activity log:** status changes, document additions, and approvals are stamped with
  timestamp and actor in an append-only log shown in the brief view.
- **The saved brief is the record of truth:** the dashboard reads saved briefs (falling
  back to the AMS for un-touched clients) so work done in the brief is immediately
  visible on the portfolio view.

**Not yet implemented:** term roll-forward (archive current term, advance dates +1yr,
roll premiums, reset status). This requires `dates.add_one_year()`, which was not built.

## With another week

Wire a real AMS integration with an explicit reconciliation rule; add per-carrier
extraction examples and field-level confidence; store the AI-proposed initial extraction
alongside the agent-final so the first-generation diff is also preserved; and extend the
schema to multi-line accounts (BOP + auto + workers' comp), which is where most
commercial clients actually sit.
