"""
dates.py
--------
Deterministic renewal staging and premium trend. Pure arithmetic — not the model's job.

Thresholds: URGENT <=45d, ACT_NOW <=120d, HEADS_UP <=150d, FUTURE >150d, UNKNOWN if
no parseable expiration. Rationale: agencies should start the renewal conversation
~120 days out; 150 days is the early radar.
"""

import datetime as dt

URGENT_DAYS = 45
ACT_NOW_DAYS = 120
HEADS_UP_DAYS = 150

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y",
    "%m.%d.%Y", "%b %d %Y", "%b %d, %Y", "%B %d %Y", "%B %d, %Y",
)


def parse_iso(date_str):
    """Parse a date string in any of several formats. Returns a date or None."""
    if not date_str or str(date_str).strip() in ("", "NOT_FOUND"):
        return None
    s = str(date_str).strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def days_until(date_str, today=None):
    """Whole days from today until the given date string. -1 if unparseable."""
    today = today or dt.date.today()
    target = parse_iso(date_str)
    if target is None:
        return -1
    return (target - today).days


def format_us(date_str):
    """
    Format any parseable date string as MM/DD/YYYY for display. The source data
    carries five different date formats; this gives the UI one consistent look.
    Returns "" if the value can't be parsed (so empty/NOT_FOUND render blank).
    """
    d = parse_iso(date_str)
    if d is None:
        return ""
    return d.strftime("%m/%d/%Y")


def to_iso(date_str):
    """
    Normalize any parseable date string to ISO YYYY-MM-DD for storage. We keep
    ISO as the internal canonical (sorts correctly, matches the schema) while the
    UI shows US format. Passes the value through unchanged if it can't be parsed,
    so a sentinel like "NOT_FOUND" survives a round-trip rather than vanishing.
    """
    d = parse_iso(date_str)
    if d is None:
        return date_str
    return d.isoformat()


def renewal_stage(days):
    if days < 0:
        return "UNKNOWN"
    if days <= URGENT_DAYS:
        return "URGENT"
    if days <= ACT_NOW_DAYS:
        return "ACT_NOW"
    if days <= HEADS_UP_DAYS:
        return "HEADS_UP"
    return "FUTURE"


def premium_trend(current, prior):
    if not current or not prior:
        return "UNKNOWN"
    delta = current - prior
    pct = (delta / prior) * 100 if prior else 0
    if abs(pct) < 1:
        return f"FLAT (${current:,})"
    direction = "UP" if delta > 0 else "DOWN"
    return f"{direction} {pct:+.0f}% (${prior:,} -> ${current:,})"


STAGE_LABELS = {
    "URGENT": "Urgent - act immediately",
    "ACT_NOW": "Act now - proactive window",
    "HEADS_UP": "Heads up - on the radar",
    "FUTURE": "Future - no action yet",
    "UNKNOWN": "Unknown - locate policy",
}
STAGE_ORDER = ["URGENT", "ACT_NOW", "HEADS_UP", "FUTURE", "UNKNOWN"]
