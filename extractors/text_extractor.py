from __future__ import annotations

from io import BytesIO
from typing import List

import pdfplumber


def extract_text_from_pdf(pdf_bytes: bytes, max_pages: int = 5) -> str:
    """Extract text from the first N pages of a PDF."""
    if not pdf_bytes:
        return ""
    texts: List[str] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text() or ""
            texts.append(text)
    return "\n".join(texts).strip()
