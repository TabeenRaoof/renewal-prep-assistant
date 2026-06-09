"""
app.py
------
Renewal Prep Assistant — Streamlit UI.

Single page, drill-down design:
  * The Portfolio Dashboard is the home screen — every client in the book, aged,
    bucketed by how close the renewal is, plus an "add a new client" uploader.
  * Click any client row to drill into their Renewal Brief — a full-page view of
    the structured, editable brief with a "Back to dashboard" button.

There are no tabs. Navigation is a session-state router (Streamlit can't switch
tabs programmatically), so clicking a client swaps the whole page to the brief
view and Back swaps it back.

Runs with no API key (mock mode) for the 18 seeded clients. Live extraction
(adding a brand-new client from uploaded documents) needs GEMINI_API_KEY.

    streamlit run app.py
"""

import os
import re
import csv as csvmod
import glob
import io
import json
import hashlib
import difflib
import datetime as dt

import streamlit as st

from pipeline import build_brief, llm, dates
from pipeline.ingest import _load_text
from pipeline.schema import NOT_FOUND

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTS_DIR = os.path.join(HERE, "data", "clients")
AMS_CSV = os.path.join(HERE, "data", "ams_export.csv")
OUTPUTS = os.path.join(HERE, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

# The AMS/CRM export column order. New clients append a row in exactly this shape.
AMS_HEADER = [
    "client_id", "name", "company", "email", "phone", "status",
    "carrier", "policy_number", "effective_date", "expiration_date",
    "annual_premium", "last_contact", "notes",
]

STAGE_BADGE = {
    "URGENT": ("🔴", st.error),
    "ACT_NOW": ("🟠", st.warning),
    "HEADS_UP": ("🟡", st.info),
    "FUTURE": ("🟢", st.success),
    "UNKNOWN": ("⚪", st.info),
}

# Industry-friendly label for "no claims on file" — replaces the raw NOT_FOUND
# sentinel the model emits when it finds no loss history.
NO_LOSSES = "No reported losses"

st.set_page_config(page_title="Renewal Prep Assistant", page_icon="📋", layout="wide")


# ---------------------------------------------------------------------------
# Small display / parsing helpers
# ---------------------------------------------------------------------------

def clean(v):
    """For editable text inputs: show a blank instead of the NOT_FOUND sentinel."""
    if v is None or v == NOT_FOUND:
        return ""
    return str(v)


def dash(v):
    """For dashboard table cells: show an em-dash for empty / NOT_FOUND values."""
    if v is None or str(v).strip() in ("", NOT_FOUND):
        return "—"
    return str(v)


def parse_money(text):
    """
    Pull a numeric dollar amount out of the messy ways premium is written in the
    AMS export: "$8,450", "8450", "8450/yr", "$8,450.00", "about 4.2k". Returns an
    int (whole dollars), or 0 if there's nothing parseable.
    """
    if text is None:
        return 0
    s = str(text).strip().lower()
    if s in ("", "not_found"):
        return 0

    # Handle the "4.2k" shorthand -> 4200.
    k_match = re.search(r"([\d.]+)\s*k\b", s)
    if k_match:
        try:
            return int(float(k_match.group(1)) * 1000)
        except ValueError:
            return 0

    # Otherwise strip everything that isn't a digit or decimal point.
    digits = re.sub(r"[^\d.]", "", s)
    if not digits:
        return 0
    try:
        return int(round(float(digits)))
    except ValueError:
        return 0


def format_money(n):
    """Format a number as clean annual currency: 8450 -> '$8,450'. Blank -> '—'."""
    try:
        n = int(round(float(n)))
    except (ValueError, TypeError):
        return "—"
    if n <= 0:
        return "—"
    return f"${n:,}"


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def sample_client_ids():
    if not os.path.isdir(CLIENTS_DIR):
        return []
    return sorted(os.path.basename(p) for p in glob.glob(CLIENTS_DIR + "/*") if os.path.isdir(p))


def load_sample_files(client_id):
    """Gather a client's documents on disk plus the shared AMS export."""
    files = []
    folder = os.path.join(CLIENTS_DIR, client_id)
    for p in sorted(glob.glob(folder + "/*")):
        with open(p, "rb") as fh:
            files.append((os.path.basename(p), fh.read()))
    if os.path.exists(AMS_CSV):
        with open(AMS_CSV, "rb") as fh:
            files.append(("ams_export.csv", fh.read()))
    return files


_DEFAULT_RENEWAL_STATUS = "Not Started"

def read_ams_rows():
    if not os.path.exists(AMS_CSV):
        return []
    with open(AMS_CSV, "rb") as fh:
        text = _load_text(fh.read())
    rows = list(csvmod.DictReader(io.StringIO(text)))
    today = dt.date.today()
    for r in rows:
        d = dates.days_until(r.get("expiration_date", ""), today=today)
        r["_days"] = d
        r["_stage"] = dates.renewal_stage(d)
        # Pull renewal_status from saved brief if one exists; otherwise default.
        brief_path = os.path.join(OUTPUTS, f"{r.get('client_id', '')}_brief.json")
        if os.path.exists(brief_path):
            try:
                with open(brief_path) as fh:
                    saved = json.load(fh)
                raw = saved.get("renewal_status", _DEFAULT_RENEWAL_STATUS)
                # Strip the "Renewal Process " prefix for compact display in the table.
                r["_renewal_status"] = raw.replace("Renewal Process ", "").strip()
            except Exception:
                r["_renewal_status"] = _DEFAULT_RENEWAL_STATUS
        else:
            r["_renewal_status"] = _DEFAULT_RENEWAL_STATUS
    return rows


def _client_display_map(rows):
    """Map client_id -> 'Business Name  (CL-XXXX)' for human-readable selectors."""
    m = {}
    for r in rows:
        cid = r.get("client_id", "")
        name = r.get("company") or r.get("name") or cid
        m[cid] = f"{name}  ({cid})"
    return m


def saved_brief_path(client_id):
    return os.path.join(OUTPUTS, f"{client_id}_brief.json")


def _next_client_id(rows):
    """Next CL-#### id, one past the highest numeric suffix already in the book."""
    highest = 2000
    for r in rows:
        cid = r.get("client_id", "")
        m = re.match(r"CL-(\d+)", cid)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"CL-{highest + 1}"


def _profile_to_ams_row(client_id, profile):
    """Map an extracted profile into a single AMS export row (dict by column)."""
    acc = profile["account"]
    pol = profile["policy"]
    return {
        "client_id": client_id,
        "name": clean(acc.get("contact_name")),
        "company": clean(acc.get("business_name")),
        "email": clean(acc.get("contact_email")),
        "phone": clean(acc.get("contact_phone")),
        "status": "new",
        "carrier": clean(pol.get("carrier")),
        "policy_number": clean(pol.get("policy_number")),
        # Store dates in ISO; the UI formats to MM/DD/YYYY on display.
        "effective_date": dates.to_iso(pol.get("effective_date", "")),
        "expiration_date": dates.to_iso(pol.get("expiration_date", "")),
        "annual_premium": str(pol.get("annual_premium_usd") or 0),
        "last_contact": dt.date.today().isoformat(),
        "notes": f"Added via upload on {dt.date.today().isoformat()}",
    }


def _append_ams_row(row_dict):
    """Append one client row to the AMS export, writing a header if the file is new."""
    file_exists = os.path.exists(AMS_CSV)
    with open(AMS_CSV, "a", newline="") as fh:
        wr = csvmod.writer(fh)
        if not file_exists:
            wr.writerow(AMS_HEADER)
        wr.writerow([row_dict.get(col, "") for col in AMS_HEADER])


# ---------------------------------------------------------------------------
# Duplicate-client detection + file de-duplication
# ---------------------------------------------------------------------------
# When the account manager uploads documents to "add a new client," we must NOT
# blindly mint a new record. Two independent signals tell us the documents may
# belong to a client already in the book:
#   1. the extracted business name matches (or closely resembles) an existing one,
#   2. one of the uploaded files is byte-for-byte already on file for a client.
# Either one triggers a confirmation step before anything is written.

# Common legal-entity suffixes. We strip these before comparing names so that
# "Frosted Pine Bakery" and "Frosted Pine Bakery, LLC" read as the same business.
_BIZ_SUFFIXES = {
    "llc", "llp", "lllp", "lp", "pllc", "pc", "pa",
    "inc", "incorporated", "corp", "corporation",
    "co", "company", "ltd", "limited", "group", "holdings", "enterprises",
}

# How alike two normalized names must be (0..1) to be flagged as "similar".
# Deliberately sensitive: a false positive only costs the user one extra click,
# while a false negative is exactly the duplicate-client bug we're fixing.
_NAME_SIMILARITY_THRESHOLD = 0.84


def _normalize_name(name):
    """
    Lowercase, drop punctuation, and strip trailing legal-entity suffixes, so
    name comparison keys on the distinctive part of the business name.
    """
    s = (name or "").lower()
    # Replace anything that isn't a letter or digit with a space.
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t]
    # Peel off trailing entity-type words ("... bakery llc co" -> "... bakery").
    while tokens and tokens[-1] in _BIZ_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _file_digest(data):
    """Stable content fingerprint for a file's bytes."""
    return hashlib.sha256(data).hexdigest()


def _folder_digests(folder):
    """Map sha256 -> filename for every file already in a client's folder."""
    digests = {}
    if os.path.isdir(folder):
        for p in sorted(glob.glob(folder + "/*")):
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    digests[_file_digest(fh.read())] = os.path.basename(p)
    return digests


def _match_existing_name(name, rows):
    """
    Compare an extracted business name against every client in the book.

    Returns (exact_cid_or_None, similar) where `similar` is a list of
    (client_id, display_name, score) for near-matches, best first. An exact
    match is the same name after normalization; a similar match is a high
    fuzzy ratio OR one normalized name containing the other (with a length
    guard so trivial substrings don't trigger).
    """
    target = _normalize_name(name)
    exact = None
    similar = []
    if not target:
        return None, []
    for r in rows:
        cid = r.get("client_id", "")
        disp = r.get("company") or r.get("name") or cid
        cand = _normalize_name(disp)
        if not cand:
            continue
        if cand == target:
            exact = cid
            continue
        ratio = difflib.SequenceMatcher(None, target, cand).ratio()
        shorter = min(target, cand, key=len)
        contained = len(shorter) >= 4 and (target in cand or cand in target)
        if ratio >= _NAME_SIMILARITY_THRESHOLD or contained:
            similar.append((cid, disp, round(ratio, 2)))
    similar.sort(key=lambda x: x[2], reverse=True)
    return exact, similar


def _match_existing_files(files, client_ids):
    """
    Find clients whose folder ALREADY contains one of the uploaded files
    (matched by content hash, so a rename can't disguise a duplicate).

    Returns {client_id: [matching_filename, ...]}. This catches the case where
    the name failed to extract but the very same document is already on file.
    """
    upload_digests = {}
    for fname, data in files:
        upload_digests.setdefault(_file_digest(data), fname)
    hits = {}
    for cid in client_ids:
        folder = os.path.join(CLIENTS_DIR, cid)
        for digest, existing_name in _folder_digests(folder).items():
            if digest in upload_digests:
                hits.setdefault(cid, []).append(existing_name)
    return hits


def _save_files_dedup(folder, files):
    """
    Write `files` into `folder`, skipping any whose content already exists there
    (by hash). On a name collision with DIFFERENT content, keep both by adding a
    " (2)" suffix so nothing is silently overwritten. Returns (added, skipped).
    """
    os.makedirs(folder, exist_ok=True)
    existing_digests = _folder_digests(folder)
    existing_names = {os.path.basename(p) for p in glob.glob(folder + "/*")}
    added, skipped = [], []
    for fname, data in files:
        digest = _file_digest(data)
        if digest in existing_digests:
            skipped.append(fname)  # identical file already on file -> skip
            continue
        target_name = fname
        if target_name in existing_names:
            # Same name, new content: preserve both rather than clobber.
            stem, ext = os.path.splitext(fname)
            n = 2
            while f"{stem} ({n}){ext}" in existing_names:
                n += 1
            target_name = f"{stem} ({n}){ext}"
        with open(os.path.join(folder, target_name), "wb") as fh:
            fh.write(data)
        existing_names.add(target_name)
        existing_digests[digest] = target_name
        added.append(target_name)
    return added, skipped


def _display_for(cid, rows):
    """Human-readable name for a client_id, falling back to the id itself."""
    for r in rows:
        if r.get("client_id") == cid:
            return r.get("company") or r.get("name") or cid
    return cid


# ---------------------------------------------------------------------------
# Brief loading (prefer a saved brief; otherwise build from the client's docs)
# ---------------------------------------------------------------------------

def get_or_build_brief(client_id):
    """
    Return the brief dict for a client. Prefers a previously approved/saved brief
    in outputs/; otherwise builds it from the client's documents on disk. Cached
    in session state so we don't rebuild on every widget interaction.
    """
    if st.session_state.get("brief_client") == client_id and "brief" in st.session_state:
        return st.session_state["brief"], st.session_state.get("ingested_meta", {})

    saved = saved_brief_path(client_id)
    if os.path.exists(saved):
        with open(saved) as fh:
            brief = json.load(fh)
        meta = {}
    else:
        files = load_sample_files(client_id)
        brief_obj, ingested = build_brief(files, client_id=client_id, mock_stem=client_id)
        brief = brief_obj.model_dump()
        meta = {
            "files_seen": ingested["files_seen"],
            "native_files": ingested["native_files"],
            "notes": ingested["notes"],
        }

    st.session_state["brief"] = brief
    st.session_state["brief_client"] = client_id
    st.session_state["ingested_meta"] = meta
    return brief, meta


def go_to_brief(client_id):
    """Navigate the router to the brief view for a client."""
    st.session_state["view"] = "brief"
    st.session_state["active_client"] = client_id
    # Force a fresh load for the newly selected client.
    st.session_state.pop("brief", None)
    st.session_state.pop("brief_client", None)


def go_to_dashboard():
    st.session_state["view"] = "dashboard"
    st.session_state.pop("active_client", None)


# ---------------------------------------------------------------------------
# Guided demo / onboarding walkthrough
# ---------------------------------------------------------------------------
# A first-run prompt offers the account manager a quick tour. If they take it,
# a multi-step modal walks through the two core flows — adding a client and
# generating a brief — one screen at a time with Back / Next. It is EXPLAIN-ONLY
# on purpose: it teaches the workflow, it does not click through the UI for them.
# The tour stays reachable any time via the "📖 Demo" button in the page header,
# even if they dismissed the first-run prompt earlier.

# Each step is (title, markdown body). Plain prose, no live navigation.
_DEMO_STEPS = [
    (
        "Welcome 👋",
        "This tool turns the messy pile behind a **BOP renewal** — declaration "
        "pages, client emails, an AMS export — into a structured, editable "
        "**Renewal Prep Brief**, and ages your whole book so nothing slips.\n\n"
        "It surfaces and organizes; **you** decide and act. Nothing is ever sent "
        "to a client.\n\nThis 60-second tour covers the two things you'll do most: "
        "**adding a client** and **opening a brief**.",
    ),
    (
        "Reading the dashboard 📊",
        "The home page is your **book of business**, aged by how close each policy "
        "is to expiring:\n\n"
        "- 🔴 **Urgent** — 45 days or less\n"
        "- 🟠 **Act now** — within 120 days, the proactive window\n"
        "- 🟡 **Heads up** — within 150 days, on the radar\n\n"
        "The counters across the top tally each bucket. Work top-down — red first.",
    ),
    (
        "Adding a client ➕",
        "Open **“➕ Add a new client”** at the top of the dashboard, **drag in the "
        "documents** — the declaration page PDF plus any client emails — and click "
        "**Create client**.\n\n"
        "The tool reads them for you: it extracts the policy and account facts, "
        "normalizes the dates and premiums, and **flags anything that conflicts** "
        "instead of guessing. You type nothing by hand.\n\n"
        "> Adding a client needs a live key (`GEMINI_API_KEY`), since it reads real "
        "documents with the model. The 18 seeded clients work without one.",
    ),
    (
        "Opening a brief 📄",
        "In any table, **click a client's ID** — the link in the first column — to "
        "drill into their renewal brief. Every other cell stays plain text, so you "
        "can still select and copy from it.\n\n"
        "Opening a brief runs the pipeline: it **ingests** the documents, "
        "**extracts** the facts, **synthesizes** what the client cares about plus "
        "any genuine coverage gaps, and **computes** days-to-expiration and the "
        "premium trend.",
    ),
    (
        "Review, edit & save ✅",
        "The brief is **yours to edit** — every field is editable. Check the flagged "
        "conflicts and low-confidence fields, fix anything, then **Approve & save** "
        "to store it as that client's record of truth.\n\n"
        "Use **“← Back to dashboard”** to return. That's the whole loop — you're "
        "ready to prep a renewal.",
    ),
]


def open_demo():
    """Open the multi-step walkthrough at the first step."""
    st.session_state["demo_step"] = 0
    st.session_state["demo_open"] = True
    # Opening the tour also retires the first-run prompt for this session.
    st.session_state["welcome_shown"] = True


@st.dialog("Quick tour", width="large")
def demo_dialog():
    """Multi-step onboarding modal: Back / Next through _DEMO_STEPS."""
    total = len(_DEMO_STEPS)
    step = st.session_state.get("demo_step", 0)
    step = max(0, min(step, total - 1))  # clamp defensively
    title, body = _DEMO_STEPS[step]

    # Progress indicator at the top so the user always knows how far in they are.
    st.progress((step + 1) / total)
    st.caption(f"Step {step + 1} of {total}")
    st.markdown(f"### {title}")
    st.markdown(body)
    st.write("")

    # Navigation row. Back is disabled on the first step; the last step shows
    # "Done" (which closes the modal) instead of "Next".
    nav_back, _nav_gap, nav_next = st.columns([1, 2, 1])
    with nav_back:
        if st.button("← Back", disabled=(step == 0), use_container_width=True):
            st.session_state["demo_step"] = step - 1
            st.rerun()
    with nav_next:
        if step < total - 1:
            if st.button("Next →", type="primary", use_container_width=True):
                st.session_state["demo_step"] = step + 1
                st.rerun()
        else:
            if st.button("Done", type="primary", use_container_width=True):
                st.session_state["demo_open"] = False
                st.rerun()


@st.dialog("👋 Welcome")
def welcome_prompt():
    """First-run prompt offering the tour. Shown once per browser session."""
    st.markdown(
        "First time here? Take a **quick tour** of how to add a client and prep a "
        "renewal brief — or jump straight in."
    )
    take_it, skip_it = st.columns(2)
    with take_it:
        if st.button("Show me around", type="primary", use_container_width=True):
            open_demo()
            st.rerun()
    with skip_it:
        # "welcome_shown" is already set by the gate that opened this prompt, so
        # dismissing here (or via the dialog's ✕) won't reopen it this session.
        if st.button("Maybe later", use_container_width=True):
            st.rerun()


# ---------------------------------------------------------------------------
# Header + mode banner (rendered on every view)
# ---------------------------------------------------------------------------

# Title on the left, an always-available demo launcher pinned to the top-right.
_title_l, _title_r = st.columns([5, 1], vertical_alignment="center")
with _title_l:
    st.title("📋 Renewal Prep Assistant")
    st.caption("Commercial BOP renewals — for the account manager, never the client.")
with _title_r:
    if st.button("📖 Demo", help="Show the quick walkthrough", use_container_width=True):
        open_demo()
        st.rerun()

if llm.MODE == "mock":
    st.warning(
        "**MOCK MODE** — no API key set, returning saved sample outputs for the "
        "seeded clients. Set `GEMINI_API_KEY` to add new clients from documents.",
        icon="🧪",
    )

# One-shot status message queued by a prior action (client created / docs filed).
_flash = st.session_state.pop("flash", None)
if _flash:
    st.toast(_flash, icon="✅")


# ---------------------------------------------------------------------------
# Client table rendering (only the client_id is clickable)
# ---------------------------------------------------------------------------

# Column layouts. Weights must match between the header and the data rows.
# The first column is always the clickable client_id.
_BUCKET_WEIGHTS = [1.3, 2.2, 1.6, 1.2, 0.7, 1.1, 1.5]
_BUCKET_HEADERS = ["Client ID", "Client", "Carrier", "Expires", "Days", "Premium", "Status"]
_FULL_WEIGHTS = [1.3, 2.2, 1.6, 1.2, 0.7, 1.0, 1.1, 1.5]
_FULL_HEADERS = ["Client ID", "Client", "Carrier", "Expires", "Days", "Stage", "Premium", "Status"]


_TABLE_GRID_CSS = """
<style>
/* Compact, table-like padding inside each column cell */
div[data-testid="stColumn"] > div:first-child {
    padding-top: 4px;
    padding-bottom: 4px;
}
/* Remove the default large gap between column rows so gridlines sit flush */
div[data-testid="stHorizontalBlock"] {
    gap: 0.25rem;
    margin-bottom: 0 !important;
}
/* Client ID link button: bold, larger than row text, underlined */
div[data-testid="stColumn"] button[kind="tertiary"] {
    padding: 0;
    margin: 0;
    height: auto;
    min-height: 0;
    font-size: 1.1rem;
    font-weight: 700;
    text-decoration: underline;
    transition: color 0.15s ease, transform 0.15s ease;
}
/* Hover: shift to green and pop out slightly */
div[data-testid="stColumn"] button[kind="tertiary"]:hover {
    color: #2e7d32 !important;
    transform: scale(1.06);
}
</style>
"""


def render_client_rows(bucket, key_prefix, full=False):
    """
    Render a click-to-open client table built from columns (not st.dataframe), so
    ONLY the client_id is clickable — a link-style button. Every other cell is
    plain text the agent can select and copy. No row checkboxes.
    """
    if not bucket:
        return

    st.markdown(_TABLE_GRID_CSS, unsafe_allow_html=True)

    weights = _FULL_WEIGHTS if full else _BUCKET_WEIGHTS
    headers = _FULL_HEADERS if full else _BUCKET_HEADERS

    # Header row — bold labels with a thicker underline to separate from data.
    hcols = st.columns(weights, vertical_alignment="center")
    for hc, label in zip(hcols, headers):
        hc.markdown(f"**{label}**")
    st.markdown(
        '<hr style="margin:0 0 2px 0; border:none; border-top:2px solid #aaa;">',
        unsafe_allow_html=True,
    )

    # One row per client, each separated by a thin gridline.
    for r in bucket:
        cid = r.get("client_id", "")
        cols = st.columns(weights, vertical_alignment="center")

        # The client_id is the only clickable thing — a borderless (tertiary)
        # button styled like a link. Clicking it drills into the brief.
        if cols[0].button(cid or "—", key=f"open_{key_prefix}_{cid}", type="tertiary"):
            go_to_brief(cid)
            st.rerun()

        client = r.get("company") or r.get("name") or cid
        carrier = dash(r.get("carrier", ""))
        expires = dates.format_us(r.get("expiration_date", "")) or "—"
        days = r["_days"]
        premium = format_money(parse_money(r.get("annual_premium", "")))
        renewal_status = r.get("_renewal_status", _DEFAULT_RENEWAL_STATUS)

        if full:
            values = [client, carrier, expires, str(days), r["_stage"], premium, renewal_status]
        else:
            values = [client, carrier, expires, str(days), premium, renewal_status]

        for col, val in zip(cols[1:], values):
            col.write(val)

        # Thin horizontal gridline between rows.
        st.markdown(
            '<hr style="margin:0; border:none; border-top:1px solid #e0e0e0;">',
            unsafe_allow_html=True,
        )


def render_bucket(title, bucket, key_prefix):
    if not bucket:
        return
    st.markdown(f"**{title}**")
    sorted_bucket = sorted(bucket, key=lambda x: x["_days"])
    render_client_rows(sorted_bucket, key_prefix, full=False)
    st.write("")  # a little breathing room between buckets


# ---------------------------------------------------------------------------
# Add-client actions: create-new vs. merge-into-existing
# ---------------------------------------------------------------------------

def _reset_add_flow():
    """Clear the pending upload and reset the file uploader to an empty state."""
    st.session_state.pop("pending_add", None)
    # The uploader is keyed by a generation counter; bumping it (a plain, non-
    # widget key) forces a fresh, empty uploader on the next run without the
    # "can't modify an instantiated widget" error.
    st.session_state["uploader_gen"] = st.session_state.get("uploader_gen", 0) + 1


def _create_new_client(files, brief, rows):
    """Mint a brand-new client: assign the next id, file the docs, append the AMS
    row, save the brief, then open it. Only reached once we're sure it isn't a
    duplicate of someone already in the book."""
    new_id = _next_client_id(rows)
    brief = dict(brief)
    brief["client_id"] = new_id

    dest = os.path.join(CLIENTS_DIR, new_id)
    added, _skipped = _save_files_dedup(dest, files)
    _append_ams_row(_profile_to_ams_row(new_id, brief["profile"]))
    with open(saved_brief_path(new_id), "w") as fh:
        json.dump(brief, fh, indent=2)

    name = clean(brief["profile"]["account"]["business_name"]) or new_id
    st.session_state["flash"] = f"Created {name} ({new_id}) from {len(added)} document(s)."
    _reset_add_flow()
    go_to_brief(new_id)
    st.rerun()


def _merge_into_existing(cid, files, rows):
    """File the uploaded documents into an existing client's folder, skipping any
    that are already on record. Per the chosen policy, the client's saved brief is
    left untouched — only the document folder grows."""
    dest = os.path.join(CLIENTS_DIR, cid)
    added, skipped = _save_files_dedup(dest, files)
    name = _display_for(cid, rows)
    if added:
        msg = f"Added {len(added)} document(s) to {name} ({cid})."
        if skipped:
            msg += f" Skipped {len(skipped)} already on file."
    else:
        msg = (
            f"Nothing new to add for {name} ({cid}) — all {len(skipped)} uploaded "
            "file(s) were already on record."
        )
    st.session_state["flash"] = msg
    _reset_add_flow()
    go_to_brief(cid)
    st.rerun()


def _render_add_reconcile(pending, rows):
    """Step 2 of add-client. A duplicate or look-alike was detected; the account
    manager decides whether to file the docs under an existing client or create a
    new one. Nothing has been written to disk yet at this point."""
    name = pending["name"]
    display_name = name or "this upload"
    files = pending["files"]
    brief = pending["brief"]
    exact = pending["exact"]
    file_hits = pending["file_hits"]   # {cid: [filenames already on record]}
    similar = pending["similar"]       # [(cid, display_name, score)]

    # "Strong" duplicates: identical business name, or the very same file already
    # on record for a client. Either is near-certain evidence it's the same client.
    strong = []
    for cid in ([exact] if exact else []) + list(file_hits.keys()):
        if cid and cid not in strong:
            strong.append(cid)

    if strong:
        st.warning(
            "These documents look like they belong to a client already in your "
            "book. Client names are kept unique, so this won't be added as a "
            "separate client — file the documents under the existing client instead.",
            icon="⚠️",
        )
        for cid in strong:
            disp = _display_for(cid, rows)
            reasons = []
            if cid == exact:
                reasons.append("same business name")
            if cid in file_hits:
                reasons.append("already has: " + ", ".join(file_hits[cid]))
            st.markdown(f"**{disp}**  ({cid}) — {'; '.join(reasons)}")
            if st.button(
                f"📎 Add these documents to {cid}",
                key=f"merge_{cid}", type="primary", use_container_width=True,
            ):
                _merge_into_existing(cid, files, rows)
        st.divider()
        if st.button("Cancel", key="add_cancel_strong", use_container_width=True):
            _reset_add_flow()
            st.rerun()
        return

    # Otherwise: similar (not identical) names. Make the user confirm it's genuinely
    # a new client before we mint a record that resembles an existing one.
    st.warning(
        f"**{display_name}** looks similar to {len(similar)} client(s) already in "
        "your book. Please confirm whether this is one of them or a new client.",
        icon="⚠️",
    )
    for cid, disp, _score in similar:
        cols = st.columns([3, 2], vertical_alignment="center")
        cols[0].markdown(f"**{disp}**  ({cid})")
        if cols[1].button(f"Add to {cid}", key=f"merge_{cid}", use_container_width=True):
            _merge_into_existing(cid, files, rows)
    st.divider()
    left, right = st.columns(2)
    with left:
        if st.button(
            f"✅ Create “{display_name}” as a new client",
            key="add_create_new", type="primary", use_container_width=True,
        ):
            _create_new_client(files, brief, rows)
    with right:
        if st.button("Cancel", key="add_cancel_similar", use_container_width=True):
            _reset_add_flow()
            st.rerun()


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

def render_dashboard():
    rows = read_ams_rows()

    # ---- Add a new client (upload -> extract -> reconcile -> create/merge) ----
    # Two-step flow. Step 1 reads the documents and checks them against the book.
    # If the upload looks like an existing client (same/similar name, or a file
    # already on record), we DON'T create anything — we stash the result in
    # session state and render the reconcile UI (step 2) for a human decision.
    pending = st.session_state.get("pending_add")
    with st.expander("➕ Add a new client", expanded=bool(pending)):
        if pending is not None:
            _render_add_reconcile(pending, rows)
        else:
            st.caption(
                "Drag and drop the client's documents (declaration page PDF, emails). "
                "The tool reads them, checks the name and files against your book to "
                "avoid duplicates, then files everything — you don't enter details by hand."
            )
            # The uploader key carries a generation suffix so we can reset it to
            # empty after an add by bumping the counter (see _reset_add_flow).
            gen = st.session_state.get("uploader_gen", 0)
            uploaded = st.file_uploader(
                "Client documents",
                accept_multiple_files=True,
                type=["pdf", "txt", "eml", "md", "csv", "docx"],
                key=f"new_client_uploader_{gen}",
            )
            if llm.MODE == "mock":
                st.info(
                    "Adding a client reads messy documents with the model, which needs a "
                    "live key. Set `GEMINI_API_KEY` and restart to enable this. (The 18 "
                    "seeded clients below work fully in mock mode.)",
                    icon="🔑",
                )

            create_disabled = (llm.MODE == "mock") or (not uploaded)
            if st.button("Add client", type="primary", disabled=create_disabled):
                new_files = [(f.name, f.read()) for f in uploaded]
                try:
                    with st.spinner("Reading documents and checking your book for duplicates..."):
                        brief_obj, _ = build_brief(new_files, client_id="PENDING", mock_stem=None)
                        brief = brief_obj.model_dump()
                        name = clean(brief["profile"]["account"]["business_name"])
                        exact, similar = _match_existing_name(name, rows)
                        file_hits = _match_existing_files(
                            new_files, [r.get("client_id", "") for r in rows]
                        )
                except Exception as _exc:
                    st.error(f"**Extraction failed:** {type(_exc).__name__}: {_exc}", icon="🚨")
                    # Show the full cause chain so we can diagnose without needing logs.
                    cause = getattr(_exc, "__cause__", None) or getattr(_exc, "__context__", None)
                    if cause:
                        st.error(f"**Caused by:** {type(cause).__name__}: {cause}")
                    st.stop()

                if not exact and not similar and not file_hits:
                    # Clean — no resemblance to anyone in the book. Create directly.
                    _create_new_client(new_files, brief, rows)
                else:
                    # Hold for a human decision in step 2 (nothing written yet).
                    st.session_state["pending_add"] = {
                        "files": new_files,
                        "brief": brief,
                        "name": name,
                        "exact": exact,
                        "similar": similar,
                        "file_hits": file_hits,
                    }
                    st.rerun()

    if not rows:
        st.error("No AMS export found. Run `python data/gen_data.py` first.")
        return

    urgent = [r for r in rows if r["_stage"] == "URGENT"]
    act = [r for r in rows if r["_stage"] == "ACT_NOW"]
    heads = [r for r in rows if r["_stage"] == "HEADS_UP"]

    st.markdown("#### Book of business")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total clients", len(rows))
    k2.metric("🔴 Urgent (≤45d)", len(urgent))
    k3.metric("🟠 Act now (≤120d)", len(act))
    k4.metric("🟡 Heads up (≤150d)", len(heads))
    st.markdown(
        '<p style="color:#2e7d32; font-size:1rem; font-weight:600; margin:0 0 4px 0;">'
        "Click a client's ID to open their renewal brief.</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    render_bucket("🔴 Urgent — act immediately", urgent, "urgent")
    render_bucket("🟠 Act now — proactive window", act, "act")
    render_bucket("🟡 Heads up — on the radar", heads, "heads")

    with st.expander("Show full book"):
        sorted_all = sorted(rows, key=lambda x: (x["_days"] if x["_days"] >= 0 else 99999))
        render_client_rows(sorted_all, "full", full=True)


# ---------------------------------------------------------------------------
# Brief view
# ---------------------------------------------------------------------------

def render_brief_view(client_id):
    if st.button("← Back to dashboard"):
        go_to_dashboard()
        st.rerun()

    brief, meta = get_or_build_brief(client_id)
    b = brief

    _RENEWAL_STATUSES = [
        "Renewal Process Not Started",
        "Renewal Process Started",
        "Renewal Process Complete",
    ]

    st.divider()
    head_l, head_r = st.columns([2, 1])
    with head_l:
        st.subheader(clean(b["profile"]["account"]["business_name"]) or client_id)
        st.caption(clean(b["profile"]["account"]["industry"]))

        # Status dropdown — saves immediately on change, no Approve & save needed.
        current_status = b.get("renewal_status", _RENEWAL_STATUSES[0])
        if current_status not in _RENEWAL_STATUSES:
            current_status = _RENEWAL_STATUSES[0]
        new_status = st.selectbox(
            "Renewal status",
            options=_RENEWAL_STATUSES,
            index=_RENEWAL_STATUSES.index(current_status),
            key=f"status_{client_id}",
        )
        if new_status != current_status:
            b["renewal_status"] = new_status
            st.session_state["brief"] = b
            out_path = saved_brief_path(client_id)
            if os.path.exists(out_path):
                with open(out_path, "w") as fh:
                    json.dump(b, fh, indent=2)
            st.rerun()
        else:
            b["renewal_status"] = new_status

    with head_r:
        icon, fn = STAGE_BADGE.get(b["renewal_stage"], ("⚪", st.info))
        label = dates.STAGE_LABELS.get(b["renewal_stage"], b["renewal_stage"])
        when = (
            f"{b['days_to_expiration']} days to expiration"
            if b["days_to_expiration"] >= 0
            else "expiration date unknown"
        )
        fn(f"{icon}  **{label}** — {when}")

    if meta.get("native_files"):
        st.caption(f"🖼️ Scanned image(s) sent to model: {', '.join(meta['native_files'])}")

    if b["profile"]["extraction_notes"]:
        st.warning("**Needs attention:** " + b["profile"]["extraction_notes"], icon="⚠️")
    if b["profile"]["low_confidence_fields"]:
        st.info("Double-check these fields: " + ", ".join(b["profile"]["low_confidence_fields"]))

    _opps = b["insight"].get("coverage_opportunities", [])
    if _opps:
        st.success(
            f"**Coverage opportunities ({len(_opps)}):** " + "; ".join(o["gap"] for o in _opps),
            icon="💡",
        )

    st.markdown("#### Review & edit — update client info here")
    st.caption(
        "All fields are editable. Correct anything the AI got wrong, add context, "
        "update coverage or contact details, then Approve & save."
    )

    with st.form("edit_brief"):
        acc = b["profile"]["account"]
        pol = b["profile"]["policy"]
        ins = b["insight"]

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Account**")
            acc["business_name"] = st.text_input("Business name", clean(acc["business_name"]))
            acc["contact_name"] = st.text_input("Contact name", clean(acc["contact_name"]))
            acc["contact_email"] = st.text_input("Contact email", clean(acc["contact_email"]))
            acc["contact_phone"] = st.text_input("Contact phone", clean(acc["contact_phone"]))
            acc["industry"] = st.text_input("Industry", clean(acc["industry"]))
        with c2:
            st.markdown("**Policy**")
            pol["carrier"] = st.text_input("Carrier", clean(pol["carrier"]))
            pol["policy_number"] = st.text_input("Policy number", clean(pol["policy_number"]))
            _eff = st.text_input("Effective date (MM/DD/YYYY)", dates.format_us(pol["effective_date"]))
            _exp = st.text_input("Expiration date (MM/DD/YYYY)", dates.format_us(pol["expiration_date"]))
            # Store ISO internally; the field shows/accepts US format.
            pol["effective_date"] = dates.to_iso(_eff) if _eff.strip() else NOT_FOUND
            pol["expiration_date"] = dates.to_iso(_exp) if _exp.strip() else NOT_FOUND

        st.markdown("**Coverage & premium** (annual)")
        m1, m2, m3 = st.columns(3)
        with m1:
            pol["annual_premium_usd"] = st.number_input("Annual premium ($)", value=int(pol["annual_premium_usd"]), step=100)
            pol["prior_annual_premium_usd"] = st.number_input("Prior annual premium ($)", value=int(pol["prior_annual_premium_usd"]), step=100)
        with m2:
            pol["property_limit_usd"] = st.number_input("Property limit ($)", value=int(pol["property_limit_usd"]), step=1000)
            pol["deductible_usd"] = st.number_input("Deductible ($)", value=int(pol["deductible_usd"]), step=100)
        with m3:
            pol["gl_per_occurrence_usd"] = st.number_input("GL per occurrence ($)", value=int(pol["gl_per_occurrence_usd"]), step=1000)
            pol["gl_aggregate_usd"] = st.number_input("GL aggregate ($)", value=int(pol["gl_aggregate_usd"]), step=1000)

        pol["endorsements"] = [
            e.strip() for e in st.text_area(
                "Endorsements (one per line)", "\n".join(pol["endorsements"])
            ).splitlines() if e.strip()
        ]

        st.markdown("**What they care about**")
        care_rows = ins["what_they_care_about"] or [{"point": "", "evidence": ""}]
        edited_care = st.data_editor(
            care_rows, num_rows="dynamic", use_container_width=True, key="care_editor",
            column_config={"point": "What they care about", "evidence": "Evidence"},
        )

        ins["open_issues"] = [
            s.strip() for s in st.text_area(
                "Open issues (one per line)", "\n".join(ins["open_issues"])
            ).splitlines() if s.strip()
        ]
        # Loss history: show the friendly label instead of the NOT_FOUND sentinel.
        _loss_current = ins["loss_history_mentioned"]
        _loss_shown = NO_LOSSES if (not _loss_current or _loss_current == NOT_FOUND) else _loss_current
        _loss_input = st.text_input("Loss history / prior claims", _loss_shown)
        # If the agent leaves the friendly placeholder, keep the sentinel internally.
        ins["loss_history_mentioned"] = NOT_FOUND if _loss_input.strip() in ("", NO_LOSSES) else _loss_input

        st.markdown("**Coverage opportunities** (real gaps in the existing policy — adequacy, not sales)")
        opp_rows = ins.get("coverage_opportunities") or [{"gap": "", "rationale": ""}]
        edited_opps = st.data_editor(
            opp_rows, num_rows="dynamic", use_container_width=True, key="opp_editor",
            column_config={"gap": "Coverage gap", "rationale": "Why it's a real gap"},
        )

        st.markdown("**Suggested approach** (internal — not client-facing)")
        ins["suggested_approach"] = [
            s.strip() for s in st.text_area(
                "Suggestions (one per line)", "\n".join(ins["suggested_approach"]), height=120
            ).splitlines() if s.strip()
        ]
        ins["summary"] = st.text_area("Agent summary", clean(ins["summary"]), height=80)

        submitted = st.form_submit_button("✅ Approve & save brief", type="primary")

    if submitted:
        ins["what_they_care_about"] = [
            {"point": r.get("point", ""), "evidence": r.get("evidence", "")}
            for r in edited_care if r.get("point")
        ]
        ins["coverage_opportunities"] = [
            {"gap": r.get("gap", ""), "rationale": r.get("rationale", "")}
            for r in edited_opps if r.get("gap")
        ]
        b["days_to_expiration"] = dates.days_until(pol["expiration_date"])
        b["renewal_stage"] = dates.renewal_stage(b["days_to_expiration"])
        b["premium_trend"] = dates.premium_trend(
            pol["annual_premium_usd"], pol["prior_annual_premium_usd"]
        )
        b["approved_at"] = dt.datetime.now().isoformat(timespec="seconds")
        # Carry the current dropdown value into the saved brief.
        b["renewal_status"] = st.session_state.get(f"status_{client_id}", b.get("renewal_status", "Renewal Process Not Started"))

        out_path = saved_brief_path(client_id)
        with open(out_path, "w") as fh:
            json.dump(b, fh, indent=2)

        # Keep the cached copy in sync.
        st.session_state["brief"] = b

        st.success(
            f"Saved to {os.path.relpath(out_path, HERE)}  ·  "
            f"Stage: {b['renewal_stage']}  ·  Premium: {b['premium_trend']}"
        )
        st.download_button(
            "⬇️ Download brief (JSON)",
            data=json.dumps(b, indent=2),
            file_name=f"{client_id}_brief.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_view = st.session_state.get("view", "dashboard")
if _view == "brief" and st.session_state.get("active_client"):
    render_brief_view(st.session_state["active_client"])
else:
    render_dashboard()


# ---------------------------------------------------------------------------
# Onboarding overlays
# ---------------------------------------------------------------------------
# Only one dialog may be open per run, so these are mutually exclusive: the
# walkthrough wins if it's open, otherwise the first-run welcome prompt shows
# exactly once per session. We flip "welcome_shown" the moment we render it, so
# dismissing via the dialog's built-in ✕ retires it just like the buttons do
# (otherwise the gate would immediately reopen it on the next rerun).
if st.session_state.get("demo_open"):
    demo_dialog()
elif not st.session_state.get("welcome_shown"):
    st.session_state["welcome_shown"] = True
    welcome_prompt()
