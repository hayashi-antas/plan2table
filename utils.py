"""Shared small utilities (messages, parsing)."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def single_line_message(message: object) -> str:
    """Normalize a message to a single line (collapse whitespace)."""
    return " ".join(str(message or "").split())


def exception_message(exc: Exception) -> str:
    """Get a one-line message from an exception (HTTPException detail or type name)."""
    if isinstance(exc, HTTPException):
        return single_line_message(exc.detail)
    text = single_line_message(str(exc))
    if text:
        return text
    return exc.__class__.__name__


def parse_float_or_none(value: str) -> Optional[float]:
    """Parse string to float; return None for empty or invalid."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
