"""
schema.py
---------
The data contract for a renewal prep brief.

Design choices, and why:

  * No Optional / Union types. Gemini's structured-output mode is fragile with
    nullable and union types, so every field has a concrete type and a concrete
    default. Missing values use explicit sentinels ("NOT_FOUND" for text, 0 for
    money) rather than null. This is also friendlier for the human reviewer: a
    field that says NOT_FOUND is clearly "the model didn't find this," not an
    accidental blank.

  * Money is normalized to integers (USD). The model is told to convert "$8,450",
    "about 4.2k", "14,200" etc. into a plain integer. Date math and the dashboard
    need numbers, not strings.

  * Dates are ISO strings. The model normalizes the five date formats in the
    source documents to YYYY-MM-DD. Parsing/aging happens in code (dates.py), not
    in the model, so it's testable and deterministic.

The two LLM calls map to two models:
  ExtractedProfile  <- extract.py     (facts pulled from the documents)
  RenewalInsight    <- synthesize.py  (judgement built from the unstructured signal)

RenewalBrief is the assembled result the UI renders and the agent edits.
"""

from typing import List
from pydantic import BaseModel, Field

NOT_FOUND = "NOT_FOUND"


class AccountInfo(BaseModel):
    business_name: str = Field(default=NOT_FOUND, description="Legal/DBA name of the insured business")
    contact_name: str = Field(default=NOT_FOUND, description="Primary human contact, if any")
    contact_email: str = Field(default=NOT_FOUND)
    contact_phone: str = Field(default=NOT_FOUND)
    industry: str = Field(default=NOT_FOUND, description="Best guess at the business type, e.g. 'HVAC contractor'")


class PolicyInfo(BaseModel):
    carrier: str = Field(default=NOT_FOUND)
    policy_number: str = Field(default=NOT_FOUND)
    effective_date: str = Field(default=NOT_FOUND, description="ISO YYYY-MM-DD")
    expiration_date: str = Field(default=NOT_FOUND, description="ISO YYYY-MM-DD")
    annual_premium_usd: int = Field(default=0, description="Current annual premium as a plain integer, 0 if unknown")
    prior_annual_premium_usd: int = Field(default=0, description="Previous term premium if shown, else 0")
    property_limit_usd: int = Field(default=0)
    gl_per_occurrence_usd: int = Field(default=0)
    gl_aggregate_usd: int = Field(default=0)
    deductible_usd: int = Field(default=0)
    endorsements: List[str] = Field(default_factory=list)


class ExtractedProfile(BaseModel):
    account: AccountInfo = Field(default_factory=AccountInfo)
    policy: PolicyInfo = Field(default_factory=PolicyInfo)
    source_documents: List[str] = Field(default_factory=list)
    low_confidence_fields: List[str] = Field(default_factory=list)
    extraction_notes: str = Field(default="")


class CarePoint(BaseModel):
    point: str = Field(description="One thing this client appears to care about")
    evidence: str = Field(default="")


class Opportunity(BaseModel):
    gap: str = Field(description="A real coverage gap or adequacy concern tied to the EXISTING policy")
    rationale: str = Field(default="")


class RenewalInsight(BaseModel):
    what_they_care_about: List[CarePoint] = Field(default_factory=list)
    open_issues: List[str] = Field(default_factory=list)
    loss_history_mentioned: str = Field(default=NOT_FOUND)
    coverage_opportunities: List[Opportunity] = Field(
        default_factory=list,
        description="Light, REAL coverage gaps tied to the existing policy. Empty if none.",
    )
    suggested_approach: List[str] = Field(default_factory=list)
    summary: str = Field(default="")


class RenewalBrief(BaseModel):
    client_id: str = Field(default=NOT_FOUND)
    profile: ExtractedProfile = Field(default_factory=ExtractedProfile)
    insight: RenewalInsight = Field(default_factory=RenewalInsight)
    days_to_expiration: int = Field(default=-1)
    renewal_stage: str = Field(default="UNKNOWN")
    premium_trend: str = Field(default="UNKNOWN")
