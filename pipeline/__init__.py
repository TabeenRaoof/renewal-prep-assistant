"""
Pipeline orchestration. build_brief() is the one call the app makes.
"""

from .ingest import ingest_files
from .extract import extract_profile
from .synthesize import synthesize_insight
from .schema import RenewalBrief
from . import dates
from . import llm


def build_brief(files, client_id="", mock_stem=None, today=None):
    """
    files     : list of (filename, bytes)
    client_id : reference id (used for fixtures + labelling)
    mock_stem : fixture prefix, e.g. "CL-2007" -> loads CL-2007.extract.json
                and CL-2007.synthesize.json
    """
    ingested = ingest_files(files)

    ex_name = f"{mock_stem}.extract" if mock_stem else None
    sy_name = f"{mock_stem}.synthesize" if mock_stem else None

    profile = extract_profile(ingested, client_id=client_id, mock_name=ex_name)
    insight = synthesize_insight(ingested, profile, mock_name=sy_name)

    days = dates.days_until(profile.policy.expiration_date, today=today)
    stage = dates.renewal_stage(days)
    trend = dates.premium_trend(
        profile.policy.annual_premium_usd, profile.policy.prior_annual_premium_usd
    )

    return RenewalBrief(
        client_id=client_id or "NOT_FOUND",
        profile=profile,
        insight=insight,
        days_to_expiration=days,
        renewal_stage=stage,
        premium_trend=trend,
    ), ingested
