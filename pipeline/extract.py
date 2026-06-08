"""
extract.py
----------
LLM call 1: documents -> ExtractedProfile.

The prompt handles the three things that break extraction on real agency data:
normalize money to integers, normalize dates to ISO, and flag conflicts rather
than guessing silently.
"""

from .schema import ExtractedProfile
from . import llm

_SYSTEM = """You are a commercial-lines insurance assistant helping an agent prepare for a \
Business Owners Policy (BOP) renewal. You are given messy source material for ONE client: \
a policy declaration page, client emails, and a CRM/AMS row. Extract the policy and account \
facts into the required JSON schema.

Rules:
- Money fields are plain integers in USD. Convert any notation: "$8,450" -> 8450, \
"14,200" -> 14200, "about 4.2k" -> 4200, "1800/yr" -> 1800. If a value is absent, use 0.
- Dates must be ISO YYYY-MM-DD. The source uses many formats (4/29/2024, 2024-04-02, \
"Apr 10 2024", 05/01/24, 04.29.2024). Convert them. If absent, use "NOT_FOUND".
- For text fields with no value, use "NOT_FOUND". Never invent data.
- If two documents disagree (for example an email says the business expanded but the \
declaration limit is unchanged), record it in extraction_notes and add the affected field \
to low_confidence_fields. Do NOT silently pick one.
- endorsements: list the named coverages/forms from the declaration page.
- source_documents: list the file names that contributed facts.
- low_confidence_fields: any field you inferred or were unsure about.

Return ONLY the JSON object."""


def extract_profile(ingested, client_id="", mock_name=None):
    prompt = (
        f"{_SYSTEM}\n\n"
        f"Client reference id: {client_id or 'unknown'}\n\n"
        f"--- SOURCE MATERIAL START ---\n{ingested['text']}\n--- SOURCE MATERIAL END ---"
    )
    if ingested["native_files"]:
        prompt += (
            "\n\nNote: one or more declaration pages were scanned images with no text layer; "
            "they are attached as documents. Read them for the policy facts."
        )

    raw = llm.generate_structured(
        prompt,
        ExtractedProfile,
        pdf_parts=ingested["pdf_parts"],
        mock_name=mock_name,
    )
    return ExtractedProfile.model_validate(raw)
