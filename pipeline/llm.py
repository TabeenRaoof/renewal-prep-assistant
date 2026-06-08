"""
llm.py
------
Provider seam. Everything else calls generate_structured() and never touches a
provider SDK directly.

Live path: google-genai (NOT the deprecated google-generativeai).
  from google import genai

Mock path: returns fixture JSON. The whole app runs with no API key in mock mode —
useful for demos and for graders who haven't set a key.

THE BIG CAVEAT: the live path is written against current SDK docs but has never
actually executed (sandbox couldn't reach Gemini). Treat the first live run as a
real test. Things most likely to need adjustment: .parsed vs .text handling,
nested-list schema acceptance, and Part.from_bytes with real PDF bytes.
"""

import os
import json

_FORCE_MOCK = os.environ.get("RENEWAL_MOCK", "").lower() in ("1", "true", "yes")
_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODE = "mock" if (_FORCE_MOCK or not _API_KEY) else "live"
MODEL = os.environ.get("RENEWAL_MODEL", "gemini-2.5-flash")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES = os.path.join(os.path.dirname(_HERE), "fixtures")


def generate_structured(prompt, schema_model, pdf_parts=None, mock_name=None):
    """
    prompt      : instruction + text context
    schema_model: Pydantic BaseModel subclass
    pdf_parts   : list of (bytes, mime_type, filename) for scanned PDFs
    mock_name   : fixture stem, e.g. "CL-2007.extract"
    Returns a dict.
    """
    if MODE == "mock":
        return _mock_response(schema_model, mock_name)
    return _gemini_response(prompt, schema_model, pdf_parts or [])


def _gemini_response(prompt, schema_model, pdf_parts):
    import logging
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_API_KEY)

    contents = [prompt]
    for data, mime, _name in pdf_parts:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))

    # Two configs in priority order:
    #   1. Structured output (response_schema): Gemini returns a typed object.
    #      This is ideal but can fail if the SDK version rejects nested Pydantic
    #      models or if the model doesn't support the schema shape.
    #   2. Plain JSON mode (no response_schema): tell the model to return JSON
    #      and parse the text ourselves. Slower but universally accepted.
    configs = [
        types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_model,
            temperature=0.2,
        ),
        types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    ]

    last_err = None
    for config in configs:
        for _attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=MODEL, contents=contents, config=config
                )
                # Structured output populates resp.parsed; plain JSON uses resp.text.
                if getattr(resp, "parsed", None) is not None:
                    parsed = resp.parsed
                    return parsed.model_dump() if hasattr(parsed, "model_dump") else dict(parsed)
                text = getattr(resp, "text", None)
                if text:
                    return json.loads(text)
                # Both paths returned nothing — treat as a soft failure and retry.
                raise ValueError("Gemini returned an empty response (no parsed object and no text)")
            except Exception as e:
                logging.warning("Gemini attempt failed [%s]: %s", type(e).__name__, e)
                last_err = e

    # Raise preserving the original exception so Streamlit Cloud logs show the
    # real Google API error (rate limit, schema rejection, auth failure, etc.)
    raise RuntimeError(
        f"Gemini call failed after all retries ({type(last_err).__name__}): {last_err}"
    ) from last_err


def _mock_response(schema_model, mock_name):
    if mock_name:
        path = os.path.join(_FIXTURES, f"{mock_name}.json")
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
    return schema_model().model_dump()
