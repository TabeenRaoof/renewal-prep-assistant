"""
lifecycle.py
------------
Workflow vocabulary and deterministic lifecycle transforms.

This module mirrors dates.py in intent: anything that is purely mechanical
about the renewal lifecycle lives here so it's testable and auditable in code,
not scattered across the UI.

TWO-AXIS MODEL (important to keep these distinct):
  renewal_stage   — COMPUTED, from days until expiration. Answers "how urgent
                    is this by the calendar?". Lives in RenewalBrief and is
                    recalculated whenever expiration_date changes.
  renewal_status  — MANUAL, set by the account manager. Answers "where are we
                    in the process?". Only this module and the app should write it.

Statuses flow roughly left-to-right through the renewal process:

  NOT_STARTED -> IN_PROGRESS -> REMARKETING -> PENDING_CLIENT -> RENEWED
                                                               -> LOST

RENEWED and LOST are terminal for the current term; roll_term() resets to
NOT_STARTED for the next term after archiving the completed record.
"""

import copy
import datetime as dt

from . import dates


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

STATUSES = [
    "NOT_STARTED",
    "IN_PROGRESS",
    "REMARKETING",
    "PENDING_CLIENT",
    "RENEWED",
    "LOST",
]

STATUS_LABELS = {
    "NOT_STARTED":    "Not started",
    "IN_PROGRESS":    "In progress",
    "REMARKETING":    "Remarketing / shopping carriers",
    "PENDING_CLIENT": "Pending client approval",
    "RENEWED":        "Renewed",
    "LOST":           "Lost / non-renewed",
}

# Statuses that mean a renewal is closed for this term. The dashboard moves
# rows with these statuses out of the urgency buckets.
TERMINAL = {"RENEWED", "LOST"}

# Statuses the account manager can set via the dropdown. RENEWED is intentionally
# excluded here — it is only reachable through the dedicated roll_term() action
# so that the date/premium roll always accompanies the status change.
SELECTABLE = [s for s in STATUSES if s != "RENEWED"]


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------

def log_activity(brief, kind, detail, actor="agent"):
    """
    Append one ActivityEntry to brief["activity_log"].

    This is the single choke point for all audit entries so every record gets
    a consistent timestamp format. All callers should use this function rather
    than appending directly, so the log stays trustworthy.

    kind should be one of: note | status_change | doc_added | reextract | renewed
    actor is the CSR's initials or display name.
    """
    # Ensure the list exists (handles briefs loaded before this field was added).
    if "activity_log" not in brief:
        brief["activity_log"] = []

    entry = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "detail": detail,
        "actor": actor,
    }
    brief["activity_log"].append(entry)


# ---------------------------------------------------------------------------
# Term label
# ---------------------------------------------------------------------------

def make_term_label(brief):
    """
    Derive a human-readable term label like '2025-2026' from the policy dates.
    Falls back to the current year if dates are missing.
    Used to name the history archive on renewal.
    """
    pol = brief.get("profile", {}).get("policy", {})
    eff = dates.parse_iso(pol.get("effective_date", ""))
    exp = dates.parse_iso(pol.get("expiration_date", ""))
    if eff and exp:
        return f"{eff.year}-{exp.year}"
    if exp:
        return f"{exp.year - 1}-{exp.year}"
    # No dates at all — use current year as the term start.
    return f"{dt.date.today().year}-{dt.date.today().year + 1}"


# ---------------------------------------------------------------------------
# Roll term (renewal transform)
# ---------------------------------------------------------------------------

def roll_term(brief, new_premium_usd, today=None):
    """
    Advance the brief to the next policy term after a renewal is bound.

    What this does (pure function over the brief dict, no side effects):
      1. Rolls the premium history forward: prior <- current, current <- new_premium.
      2. Rolls the policy dates: effective_date <- old expiration, expiration_date <- +1yr.
      3. Recomputes the derived signals (days_to_expiration, renewal_stage, premium_trend).
      4. Resets the lifecycle status to NOT_STARTED and clears approved_at /
         status_changed_at so the fresh term starts clean.

    The caller is responsible for:
      - Archiving a deep copy of the brief BEFORE calling roll_term (the archive
        is the RENEWED snapshot; the returned brief is the fresh next-term record).
      - Logging activity entries (the caller knows what changed and who did it).
      - Saving the returned brief to disk.

    Returns the modified brief dict (same object, also returned for clarity).
    """
    pol = brief["profile"]["policy"]

    # --- 1. Roll premium history ---
    old_premium = int(pol.get("annual_premium_usd") or 0)
    pol["prior_annual_premium_usd"] = old_premium
    pol["annual_premium_usd"] = int(new_premium_usd)

    # --- 2. Roll dates ---
    old_expiration = pol.get("expiration_date", "NOT_FOUND")
    new_expiration = dates.add_one_year(old_expiration)

    # If we have a valid old expiration, the new effective date is that same date.
    # If not, leave effective_date as-is — don't invent a date.
    if old_expiration not in ("NOT_FOUND", "", None):
        pol["effective_date"] = old_expiration

    pol["expiration_date"] = new_expiration

    # --- 3. Recompute derived signals ---
    days = dates.days_until(pol["expiration_date"], today=today)
    brief["days_to_expiration"] = days
    brief["renewal_stage"] = dates.renewal_stage(days)
    brief["premium_trend"] = dates.premium_trend(
        pol["annual_premium_usd"], pol["prior_annual_premium_usd"]
    )

    # --- 4. Reset lifecycle state for the new term ---
    brief["renewal_status"] = "NOT_STARTED"
    brief["status_changed_at"] = ""
    brief["approved_at"] = ""
    # Keep term_label pointing at the OLD term until the next approval sets it.
    # The caller sets term_label on the archive copy before calling us.

    return brief
