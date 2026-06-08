"""
gen_fixtures.py
---------------
Generates <CLIENT_ID>.extract.json and <CLIENT_ID>.synthesize.json from the
roster ground truth in data/gen_data.py. Mock mode loads these so the app runs
and demos without a Gemini key.

The curated clients (CL-2001, CL-2007, CL-2002, CL-2017) have hand-crafted
synthesis outputs; others use a sensible generic fallback. Deliberately empty
coverage_opportunities on CL-2002 (frustrated client) and CL-2017 (churn risk
mid-complaint) — that restraint is intentional, not a bug.

Run directly, or via gen_data.py (which calls this automatically after
regenerating the roster so the two are always in sync):

    python data/gen_data.py          # regenerates data + fixtures
    python fixtures/gen_fixtures.py  # regenerates fixtures only
"""

import os
import sys
import json
import datetime as dt

# Reach the roster in ../data
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "data"))
from gen_data import CLIENTS, TODAY  # noqa: E402


def iso(offset):
    return (TODAY + dt.timedelta(days=offset)).strftime("%Y-%m-%d")


def source_docs(cl):
    docs = []
    if cl["has_dec"]:
        docs.append("dec.pdf")
    for i in range(1, len(cl["emails"]) + 1):
        docs.append(f"email_{i:02d}.txt")
    docs.append("ams_export.csv")
    return docs


def extract_fixture(cl):
    has_policy = cl["has_dec"] or cl["carrier"]
    low_conf = []
    notes = ""

    # The conflict trap: bakery opened a 2nd location, limit unchanged.
    if cl["id"] == "CL-2007":
        notes = ("Conflict: client email reports a second location opened in March with more "
                 "equipment, but the declaration shows a single unchanged property limit. "
                 "Coverage adequacy needs verifying.")
        low_conf.append("property_limit_usd")
    # No dec page on file -> most policy fields unknown.
    if not cl["has_dec"]:
        notes = "No declaration page on file; policy facts unknown, only the inquiry email and CRM row."
        low_conf.extend(["carrier", "policy_number", "expiration_date", "annual_premium_usd"])
    # Scanned PDF -> read via model, flag for human check.
    if cl["scanned"]:
        notes = "Declaration page was a scanned image; figures read from the image and should be spot-checked."
        low_conf.append("annual_premium_usd")

    return {
        "account": {
            "business_name": cl["biz"] or "NOT_FOUND",
            "contact_name": cl["name"] or "NOT_FOUND",
            "contact_email": cl["contact"] or "NOT_FOUND",
            "contact_phone": cl["phone"] or "NOT_FOUND",
            "industry": _industry(cl["biz"]),
        },
        "policy": {
            "carrier": cl["carrier"] or "NOT_FOUND",
            "policy_number": cl["policy_no"] or "NOT_FOUND",
            "effective_date": iso(cl["exp_offset"] - 365) if has_policy and cl["has_dec"] else "NOT_FOUND",
            "expiration_date": iso(cl["exp_offset"]) if cl["has_dec"] else "NOT_FOUND",
            "annual_premium_usd": cl["premium"] if cl["has_dec"] else 0,
            "prior_annual_premium_usd": cl["prior_premium"] or 0,
            "property_limit_usd": cl["prop_limit"] if cl["has_dec"] else 0,
            "gl_per_occurrence_usd": cl["gl_occ"] if cl["has_dec"] else 0,
            "gl_aggregate_usd": cl["gl_agg"] if cl["has_dec"] else 0,
            "deductible_usd": cl["ded"] if cl["has_dec"] else 0,
            "endorsements": list(cl["endorsements"]),
        },
        "source_documents": source_docs(cl),
        "low_confidence_fields": low_conf,
        "extraction_notes": notes,
    }


def _industry(biz):
    b = biz.lower()
    table = [
        ("heating", "HVAC contractor"), ("air", "HVAC contractor"),
        ("roofing", "Roofing contractor"), ("flooring", "Flooring contractor"),
        ("auto body", "Auto body shop"), ("auto repair", "Auto repair shop"),
        ("contracting", "General contractor"), ("bakery", "Bakery / food service"),
        ("daycare", "Childcare / daycare"), ("realty", "Real estate office"),
        ("boutique", "Retail boutique"), ("accounting", "Accounting firm"),
        ("agency", "Insurance agency"), ("dental", "Dental practice"),
        ("print", "Print shop"), ("plumbing", "Plumbing contractor"),
        ("landscaping", "Landscaping / grounds"), ("fitness", "Fitness / gym"),
    ]
    for key, label in table:
        if key in b:
            return label
    return "Small commercial business"


# Curated synthesis per client: care points, issues, suggestions. Keyed by id;
# anything not listed gets a sensible generic fallback.
SYNTH = {
    "CL-2001": {
        "care": [("Price sensitivity - flagged last year's increase as hard to absorb", "premium jumped, can't take another hike"),
                 ("Recent growth - added two service vans over winter", "added two new service vans")],
        "issues": ["Past invoice dispute (resolved per CRM)"],
        "loss": "NOT_FOUND",
        "opps": [("Confirm auto coverage for the two newly added vans",
                  "Client added two service vans; the policy carries Hired & Non-Owned Auto, which does not cover owned vehicles - verify whether commercial auto is needed.")],
        "suggest": ["Lead the renewal proactively given expiration is ~28 days out - this is urgent.",
                    "Be ready to justify any premium change; the client disputed pricing before.",
                    "Confirm the two new vans are reflected in the right auto coverage."],
        "summary": "Urgent renewal for a price-sensitive HVAC client who recently added vehicles. Expect pushback on any increase and verify auto coverage."},
    "CL-2002": {
        "care": [("Responsiveness - frustrated by unanswered follow-ups", "second time asking, nobody got back to me")],
        "issues": ["Renewal paperwork outstanding for over a month", "No phone number on file"],
        "loss": "NOT_FOUND",
        "opps": [],  # deliberately empty: client is frustrated, fix service first - not the moment
        "suggest": ["Apologize for the delay and close the paperwork loop today - retention risk.",
                    "Collect a phone number; email-only contact is slowing things down.",
                    "Daycare: confirm abuse & molestation coverage is current at renewal."],
        "summary": "A frustrated daycare client whose renewal paperwork has stalled. Service recovery first, then the renewal."},
    "CL-2007": {
        "care": [("Business growth - opened a second location with more equipment", "opened our second location, way more equipment"),
                 ("Assumes they're fully covered", "everything still covered right?")],
        "issues": ["Property limit may be inadequate for the new location (conflict with dec page)"],
        "loss": "NOT_FOUND",
        "opps": [("Increase property / business personal property limit to cover the second location",
                  "Email reports a new location with more equipment, but the dec-page limit is unchanged and covers a single location.")],
        "suggest": ["Verify the property limit covers BOTH locations before renewal - the dec page shows one.",
                    "Treat this as an upsell opportunity: more equipment likely means a higher limit.",
                    "Confirm the second location's address is added to the policy."],
        "summary": "Bakery quietly opened a second location with more equipment while the policy limit is unchanged - a coverage gap and an upsell. Verify before renewing."},
    "CL-2017": {
        "care": [("Billing accuracy - charged twice and angry", "billed twice, TWICE"),
                 ("Retention at risk - threatening to leave", "finding a new agent")],
        "issues": ["Double-charge complaint unresolved; threatened bank dispute by Friday"],
        "loss": "NOT_FOUND",
        "opps": [],  # deliberately empty: angry churn risk mid-complaint - upselling now would backfire
        "suggest": ["Resolve the double charge immediately before any renewal talk - this is a churn risk.",
                    "Acknowledge the billing error directly and explain how it happened.",
                    "Once resolved, the long runway to expiration gives room to rebuild trust."],
        "summary": "An angry client over a double charge, threatening to leave. Fix billing first; renewal is far out so focus on service recovery."},
}


def synth_fixture(cl):
    data = SYNTH.get(cl["id"])
    if data is None:
        # Generic fallback derived from the CRM note + whether an email exists.
        care = []
        note = cl["crm_note"].lower()
        if "price" in note or "hike" in note:
            care.append(("Likely price-sensitive", "CRM note flags price sensitivity"))
        if "responsiv" in note or "quick" in note or "happy" in note:
            care.append(("Values responsiveness", "CRM note: happy client, values quick service"))
        if not care:
            care.append(("General coverage continuity", "no strong signal in available material"))
        return {
            "what_they_care_about": [{"point": p, "evidence": e} for p, e in care],
            "open_issues": [],
            "loss_history_mentioned": "NOT_FOUND",
            "coverage_opportunities": [],  # no manufactured gaps without evidence
            "suggested_approach": [
                "Reach out within the proactive window to review coverage before renewal.",
                "Confirm business details haven't changed since the last term.",
            ],
            "summary": f"Routine renewal for {cl['biz']}. Limited unstructured signal; confirm details proactively.",
        }
    return {
        "what_they_care_about": [{"point": p, "evidence": e} for p, e in data["care"]],
        "open_issues": data["issues"],
        "loss_history_mentioned": data["loss"],
        "coverage_opportunities": [{"gap": g, "rationale": r} for g, r in data.get("opps", [])],
        "suggested_approach": data["suggest"],
        "summary": data["summary"],
    }


def main():
    os.makedirs(HERE, exist_ok=True)
    n = 0
    for cl in CLIENTS:
        with open(os.path.join(HERE, f"{cl['id']}.extract.json"), "w") as fh:
            json.dump(extract_fixture(cl), fh, indent=2)
        with open(os.path.join(HERE, f"{cl['id']}.synthesize.json"), "w") as fh:
            json.dump(synth_fixture(cl), fh, indent=2)
        n += 1
    print(f"Wrote fixtures for {n} clients ({n*2} files) to {HERE}")


if __name__ == "__main__":
    main()
