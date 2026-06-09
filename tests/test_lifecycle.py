"""
tests/test_lifecycle.py
-----------------------
Plain-assert tests for the deterministic lifecycle transforms. No test framework
needed — just run `python tests/test_lifecycle.py` from the renewal-prep directory.

Implementation status:
  PASSING — lifecycle.log_activity: appends a correctly-shaped entry (4 tests)
  PASSING — lifecycle.make_term_label: derives label from policy dates (3 tests)

  SKIPPED — dates.add_one_year: NOT IMPLEMENTED. These tests define the expected
            behaviour and serve as the spec for backlog item #5 (term roll-forward).
  SKIPPED — lifecycle.roll_term: NOT IMPLEMENTED. Depends on add_one_year; tests
            remain as the spec for the same backlog item.
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

class _Skipped(Exception):
    pass

def _skip(reason):
    raise _Skipped(reason)


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
# dates.add_one_year  — SKIPPED: not implemented (backlog #5)
# ---------------------------------------------------------------------------
# These tests define the expected contract and will pass once add_one_year()
# is added to dates.py. Do not delete them — they are the spec.

def test_add_one_year_standard():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("2026-07-05") == "2027-07-05"

def test_add_one_year_end_of_year():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("2026-12-31") == "2027-12-31"

def test_add_one_year_feb28_in_leap_year():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("2024-02-28") == "2025-02-28"

def test_add_one_year_feb29_leap_to_non_leap():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("2024-02-29") == "2025-02-28"

def test_add_one_year_not_found():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("NOT_FOUND") == "NOT_FOUND"

def test_add_one_year_empty():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("") == "NOT_FOUND"

def test_add_one_year_garbage():
    if not hasattr(dates, "add_one_year"):
        return _skip("dates.add_one_year not implemented")
    assert dates.add_one_year("not-a-date") == "NOT_FOUND"


# ---------------------------------------------------------------------------
# lifecycle.roll_term  — SKIPPED: not implemented (backlog #5)
# ---------------------------------------------------------------------------

def test_roll_term_premium_history():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(premium=8450, prior=7600)
    lifecycle.roll_term(b, new_premium_usd=9000)
    pol = b["profile"]["policy"]
    assert pol["prior_annual_premium_usd"] == 8450
    assert pol["annual_premium_usd"] == 9000

def test_roll_term_expiration_advances_one_year():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(expiration="2026-07-05")
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert b["profile"]["policy"]["expiration_date"] == "2027-07-05"

def test_roll_term_effective_becomes_old_expiration():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(expiration="2026-07-05")
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert b["profile"]["policy"]["effective_date"] == "2026-07-05"

def test_roll_term_renewal_stage_recomputed():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(expiration="2026-07-05")
    lifecycle.roll_term(b, new_premium_usd=9000, today=dt.date(2026, 6, 7))
    assert b["renewal_stage"] == "FUTURE"

def test_roll_term_premium_trend_recomputed():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(premium=8450, prior=7600)
    lifecycle.roll_term(b, new_premium_usd=9000)
    trend = b["premium_trend"]
    assert trend != "UNKNOWN"
    assert "UP" in trend or "DOWN" in trend or "FLAT" in trend

def test_roll_term_resets_status():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief()
    b["renewal_status"] = "PENDING_CLIENT"
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert b["renewal_status"] == "NOT_STARTED"

def test_roll_term_unknown_expiration_degrades_gracefully():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(expiration="NOT_FOUND")
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert b["profile"]["policy"]["expiration_date"] == "NOT_FOUND"
    assert b["renewal_stage"] == "UNKNOWN"

def test_roll_term_does_not_mutate_original_when_copy_used():
    if not hasattr(dates, "add_one_year"):
        return _skip("roll_term depends on add_one_year (not implemented)")
    b = _minimal_brief(premium=8450)
    archived = copy.deepcopy(b)
    lifecycle.roll_term(b, new_premium_usd=9000)
    assert archived["profile"]["policy"]["annual_premium_usd"] == 8450


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

    passed = skipped = failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  ok    {test_fn.__name__}")
            passed += 1
        except _Skipped as exc:
            print(f"  skip  {test_fn.__name__}: {exc}")
            skipped += 1
        except Exception as exc:
            print(f"  FAIL  {test_fn.__name__}: {exc}")
            failed += 1

    print(f"\n{passed} passed, {skipped} skipped (not implemented), {failed} failed")
    sys.exit(0 if failed == 0 else 1)
