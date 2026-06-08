"""
ingest.py
---------
Turn a pile of uploaded files into two things the extractor can use:

  text_blob  : all the human-readable text (emails, CSV rows, text-PDF content),
               concatenated with clear per-file headers
  pdf_parts  : raw bytes of any PDF we could NOT read locally (scanned/image
               PDFs), to be handed to the model directly

Why the split? Text extraction is free and cheap, so we do it locally whenever
we can. Only when a PDF has no usable text layer do we spend multimodal tokens
sending the document itself. That's the cost-conscious default.

Supported: .pdf, .txt, .eml, .md, .csv. DOCX is a best-effort stretch (handled
if python-docx is installed, skipped with a note otherwise).
"""

import os
import io
import csv as csvmod

import pdfplumber

# Below this many non-whitespace characters, we treat a PDF page as "no real
# text layer" and fall back to sending the document to the model.
_MIN_PDF_TEXT_CHARS = 40


def _load_pdf(data, name):
    """Return (text, needs_native). needs_native True -> send bytes to model."""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
        text = "\n".join(pages).strip()
    except Exception:
        text = ""
    if len(text.replace(" ", "").replace("\n", "")) < _MIN_PDF_TEXT_CHARS:
        return "", True  # scanned / image-only -> native fallback
    return text, False


def _load_text(data):
    """Decode a text-ish file, tolerating odd encodings."""
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_csv(data):
    """
    Render a CSV as a readable text block. Kept whole (these exports are small);
    the prompt tells the model to use only the row matching this client.
    """
    text = _load_text(data)
    rows = list(csvmod.reader(io.StringIO(text)))
    if not rows:
        return ""
    out = []
    header = rows[0]
    for r in rows[1:]:
        pairs = [f"{h}: {v}" for h, v in zip(header, r) if v.strip()]
        out.append(" | ".join(pairs))
    return "\n".join(out)


def _load_docx(data):
    """Best-effort DOCX. Returns ('', note) if the library isn't available."""
    try:
        import docx  # python-docx
    except ImportError:
        return "", "DOCX skipped (python-docx not installed)"
    try:
        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs), ""
    except Exception as e:  # noqa: BLE001
        return "", f"DOCX could not be read: {e}"


def ingest_files(files):
    """
    files: list of (filename, bytes).
    Returns a dict:
        text          : combined readable text with per-file headers
        pdf_parts     : [(bytes, mime, filename)] needing native model handling
        files_seen    : [filename, ...]
        native_files  : [filename, ...] that fell back to the model
        notes         : [str, ...] anything skipped or odd
    """
    text_chunks = []
    pdf_parts = []
    files_seen = []
    native_files = []
    notes = []

    for name, data in files:
        ext = os.path.splitext(name)[1].lower()
        files_seen.append(name)

        if ext == ".pdf":
            text, needs_native = _load_pdf(data, name)
            if needs_native:
                pdf_parts.append((data, "application/pdf", name))
                native_files.append(name)
                text_chunks.append(f"=== FILE: {name} (image/scanned PDF - sent to model) ===")
            else:
                text_chunks.append(f"=== FILE: {name} (PDF) ===\n{text}")

        elif ext in (".txt", ".eml", ".md"):
            text_chunks.append(f"=== FILE: {name} ===\n{_load_text(data)}")

        elif ext == ".csv":
            text_chunks.append(f"=== FILE: {name} (CRM/AMS rows) ===\n{_load_csv(data)}")

        elif ext == ".docx":
            body, note = _load_docx(data)
            if body:
                text_chunks.append(f"=== FILE: {name} (DOCX) ===\n{body}")
            if note:
                notes.append(note)

        else:
            notes.append(f"Unsupported file type skipped: {name}")

    return {
        "text": "\n\n".join(text_chunks).strip(),
        "pdf_parts": pdf_parts,
        "files_seen": files_seen,
        "native_files": native_files,
        "notes": notes,
    }
