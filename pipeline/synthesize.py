"""
synthesize.py
-------------
LLM call 2: unstructured signal -> RenewalInsight.

Produces what the client cares about, open issues, light coverage-gap opportunities
(evidence-gated, never manufactured), and agent suggestions. Explicitly internal —
never client-facing copy.
"""

from .schema import RenewalInsight
from . import llm

_SYSTEM = """You are helping an insurance agent prepare to RETAIN a commercial client at \
their Business Owners Policy renewal. You are given the client's emails, CRM notes, and a \
short summary of their current policy. Produce renewal insight as JSON.

Focus on RETENTION, not fraud or underwriting. Specifically:
- what_they_care_about: 1-4 things this client clearly values or worries about \
(e.g. price sensitivity, fast responses, a recent business change). Each needs short \
paraphrased evidence from the material. Do not invent motives with no basis.
- open_issues: unresolved problems to handle before the renewal conversation \
(billing disputes, undelivered documents, requests in flight).
- loss_history_mentioned: any claims/losses referenced; "NOT_FOUND" if none.
- coverage_opportunities: ONLY genuine coverage gaps tied to the EXISTING policy — for \
example a property limit that looks inadequate given a stated business change, or a \
standard endorsement clearly missing for this business type. This is light, \
service-minded adequacy checking, NOT sales. Require concrete evidence; if there is no \
real gap, return an empty list. Do NOT invent needs. Bad timing (e.g. an angry client \
mid-complaint) is a reason to stay empty.
- suggested_approach: 2-4 concrete suggestions FOR THE AGENT. Internal coaching notes, \
NOT messages to send. Never write client-facing copy. Frame as suggestions.
- summary: 2-3 sentences orienting the agent.

Return ONLY the JSON object."""


def _policy_summary(profile):
    p = profile.policy
    a = profile.account
    return (
        f"Business: {a.business_name} ({a.industry}); "
        f"Carrier: {p.carrier}; Premium: ${p.annual_premium_usd:,} "
        f"(prior ${p.prior_annual_premium_usd:,}); "
        f"Property limit ${p.property_limit_usd:,}; "
        f"Endorsements: {', '.join(p.endorsements) if p.endorsements else 'none listed'}. "
        f"Extraction notes: {profile.extraction_notes or 'none'}."
    )


def synthesize_insight(ingested, profile, mock_name=None):
    prompt = (
        f"{_SYSTEM}\n\n"
        f"CURRENT POLICY SUMMARY:\n{_policy_summary(profile)}\n\n"
        f"--- CLIENT EMAILS & NOTES START ---\n{ingested['text']}\n--- END ---"
    )
    raw = llm.generate_structured(prompt, RenewalInsight, mock_name=mock_name)
    return RenewalInsight.model_validate(raw)
