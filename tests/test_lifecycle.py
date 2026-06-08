"""
tests/test_lifecycle.py
-----------------------
Plain-assert tests for the deterministic lifecycle transforms. No test framework
needed — just run `python tests/test_lifecycle.py` from the renewal-prep directory.

Covers:
  - dates.add_one_year: standard case, Feb-29 edge case, NOT_FOUND passthrough
  - lifecycle.roll_term: premium roll, date roll, signal recompute, status reset
  - lifecycle.log_activity: appends a correctly-shaped entry
  - lifecycle.make_term_label: derives label from policy dates
"""

import sys
import os

# Allow running from the renewal-prep directory without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
import datetime as dt

from pipeline import dates
from pipeline import lifecycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_brief(client_id="CL-TEST", expiration="2026-07-05", premium=8450, prior=7600):
    """Return the smallest valid brief dict the lifecycle functions need."""
    return {
        "client_id": client_id,
        "profile": {
            "account": {"business_name": "Test Co"},
            "policy": {
                "effective_date": "2025-07-05",
                "expiration_date": expiration,
                "annual_premium_usd": premium,
                "prior_annual_premium_usd": prior,
            },
        },
        "insight": {},
        "days_to_expiration": 365,
        "renewal_stage": "FUTURE",
        "premium_trend": "UP +11% ($7,600 -> $8,450)",
        "renewal_status": "NOT_STARTED",
        "status_changed_at": "",
        "approved_at": "2026-06-01T10:00:00",
        "term_label": "2025-2026",
        "activity_log": [],
    }


# ---------------------------------------------------------------------------
# dates.add_one_year
# ---------------------------------------------------------------------------

def test_add_one_year_standard():
    result = dates.add_one_year("2026-07-05")
    assert result == "2027-07-05", f"Expected 2027-07-05, got {result}"

def test_add_one_year_end_of_year():
    result = dates.add_one_year("2026-12-31")
    assert result == "2027-12-31", f"Expected 2027-12-31, got {result}"

def test_add_one_year_feb28_in_leap_year():
    # Feb 28 in a leap year -> Feb 28 the following year (non-leap).
    result = dates.add_one_year("2024-02-28")
    assert result == "2025-02-28", f"Expected 2025-02-28, got {result}"

def test_add_one_year_feb29_leap_to_non_leap():
    # Feb 29 only exists in leap years. Next year (2025) is not a leap year,
    # so the result should fall back to Feb 28.
    result = dates.add_one_year("2024-02-29")
    assert result == "2025-02-28", f"Expected 2025-02-28, got {result}"

def test_add_one_year_not_found():
    assert dates.add_one_year("NOT_FOUND") == "NOT_FOUND"

def test_add_one_year_empty():
    assert dates.add_one_year("") == "NOT_FOUND"

def test_add_one_year_garbage():
    assert dates.add_one_year("not-a-date") == "NOT_FOUND"


# ---------------------------------------------------------------------------
# lifecycle.roll_term
# ---------------------------------------------------------------------------

def test_roll_term_premium_history():
    """prior_premium should become the old current premium."""
    b = _minimal_brief(premium=8450, prior=7600)
    lifecycle.roll_term(b, new_premium_usd=9000)
    pol = b["profile"]["policy"]
    assert pol["prior_annual_premium_usd"] == 8450, "prior should be the old current"
    assert pol["annual_premium_usd"] == 9000, "current should be the new premium"

def test_roll_term_expiration_advances_one_year():
    b = _minimal_brief(expiration="2026-07-05")
    lifecycle.roll_term(b, new_premium_usd=9000)
    pol = b["profile"]["policy"]
    assert pol["expiration_date"] == "2027-07-05", f"Got {pol['expiration_date']}"

def test_roll_term_effective_becomes_old_expiration():
    b = _minimal_brief(expiration="2026-07-05")
    lifecycle.roll_term(b, new_premium_usd=9000)
    pol = b["profile"]["policy"]
    assert pol["effective_date"] == "2026-07-05", f"Got {pol['effective_date']}"

def test_roll_term_renewal_stage_recomputed():
    # After rolling a year forward, stage should be FUTURE (>150 days away).
    b = _minimal_brief(expiration="2026-07-05")
    lifecycle.roll_term(b, new_premium_usd=9000, today=dt.date(2026, 6, 7))
    # New expiration is 2027-07-05, which is ~393 days from 2026-06-07.
    assert b["renewal_stage"] == "FUTURE", f"Got {b['renewal_stage']}"

def test_roll_term_premium_trend_recomputed():
    b = _minimal_brief(premium=8450, prior=7600)
    lifecycle.roll_term(b, new_premium_usd=9000)
    # Trend should now be based on 8450 -> 9000, not the original pair.
    trend = b["premium_trend"]
    assert trend != "UNKNOWN", "Premium trend should not be UNKNOWN"
    assert "UP" in trend or "DOWN" in trend or "FLAT" in trend, f"Unexpected trend: {trend}"

def test_roll_term_resets_status():
    b = _minimal_brief()
    b["renewal_status"] = "PENDING_CLIENT"
    b["approved_at"] = "2026-06-01T10:00:00"
    b["status_changed_at"] = "2026-05-15T09:00:00"
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert b["renewal_status"] == "NOT_STARTED", "Status should reset to NOT_STARTED"
    assert b["approved_at"] == "", "approved_at should be cleared"
    assert b["status_changed_at"] == "", "status_changed_at should be cleared"

def test_roll_term_unknown_expiration_degrades_gracefully():
    """If expiration was never extracted, roll should not crash."""
    b = _minimal_brief(expiration="NOT_FOUND")
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert b["profile"]["policy"]["expiration_date"] == "NOT_FOUND"
    assert b["renewal_stage"] == "UNKNOWN"

def test_roll_term_does_not_mutate_original_when_copy_used():
    """roll_term mutates in place; caller should deep-copy before archiving."""
    b = _minimal_brief(premium=8450)
    archived = copy.deepcopy(b)
    lifecycle.roll_term(b, new_premium_usd=9000)
    # The archived copy should be unchanged.
    assert archived["profile"]["policy"]["annual_premium_usd"] == 8450
    assert archived["renewal_status"] == "NOT_STARTED"  # wasn't RENEWED yet on archive


# ---------------------------------------------------------------------------
# lifecycle.log_activity
# ---------------------------------------------------------------------------

def test_log_activity_appends_entry():
    b = _minimal_brief()
    assert len(b["activity_log"]) == 0
    lifecycle.log_activity(b, "note", "called client about upcoming renewal", actor="TR")
    assert len(b["activity_log"]) == 1

def test_log_activity_entry_shape():
    b = _minimal_brief()
    lifecycle.log_activity(b, "status_change", "IN_PROGRESS -> REMARKETING", actor="TR")
    entry = b["activity_log"][0]
    assert entry["kind"] == "status_change"
    assert entry["detail"] == "IN_PROGRESS -> REMARKETING"
    assert entry["actor"] == "TR"
    assert "T" in entry["timestamp"]  # ISO timestamp contains 'T' between date and time

def test_log_activity_multiple_entries_ordered():
    b = _minimal_brief()
    lifecycle.log_activity(b, "note", "first note", actor="TR")
    lifecycle.log_activity(b, "note", "second note", actor="TR")
    assert b["activity_log"][0]["detail"] == "first note"
    assert b["activity_log"][1]["detail"] == "second note"

def test_log_activity_creates_list_if_missing():
    """Briefs loaded before the activity_log field was added shouldn't crash."""
    b = _minimal_brief()
    del b["activity_log"]
    lifecycle.log_activity(b, "note", "backfill test", actor="TR")
    assert len(b["activity_log"]) == 1


# ---------------------------------------------------------------------------
# lifecycle.make_term_label
# ---------------------------------------------------------------------------

def test_make_term_label_from_both_dates():
    b = _minimal_brief()
    b["profile"]["policy"]["effective_date"] = "2025-07-05"
    b["profile"]["policy"]["expiration_date"] = "2026-07-05"
    label = lifecycle.make_term_label(b)
    assert label == "2025-2026", f"Got {label}"

def test_make_term_label_from_expiration_only():
    b = _minimal_brief()
    b["profile"]["policy"]["effective_date"] = "NOT_FOUND"
    b["profile"]["policy"]["expiration_date"] = "2026-07-05"
    label = lifecycle.make_term_label(b)
    assert label == "2025-2026", f"Got {label}"

def test_make_term_label_fallback_when_no_dates():
    b = _minimal_brief()
    b["profile"]["policy"]["effective_date"] = "NOT_FOUND"
    b["profile"]["policy"]["expiration_date"] = "NOT_FOUND"
    label = lifecycle.make_term_label(b)
    # Should be something like "2026-2027" based on today
    assert "-" in label and len(label) == 9, f"Unexpected fallback label: {label}"


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_add_one_year_standard,
        test_add_one_year_end_of_year,
        test_add_one_year_feb28_in_leap_year,
        test_add_one_year_feb29_leap_to_non_leap,
        test_add_one_year_not_found,
        test_add_one_year_empty,
        test_add_one_year_garbage,
        test_roll_term_premium_history,
        test_roll_term_expiration_advances_one_year,
        test_roll_term_effective_becomes_old_expiration,
        test_roll_term_renewal_stage_recomputed,
        test_roll_term_premium_trend_recomputed,
        test_roll_term_resets_status,
        test_roll_term_unknown_expiration_degrades_gracefully,
        test_roll_term_does_not_mutate_original_when_copy_used,
        test_log_activity_appends_entry,
        test_log_activity_entry_shape,
        test_log_activity_multiple_entries_ordered,
        test_log_activity_creates_list_if_missing,
        test_make_term_label_from_both_dates,
        test_make_term_label_from_expiration_only,
        test_make_term_label_fallback_when_no_dates,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  ok  {test_fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL  {test_fn.__name__}: {exc}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
