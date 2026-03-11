"""Shared types and utilities for PDF/OCR extractors (WordBox, clustering, text normalization)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

# -----------------------------------------------------------------------------
# Shared dataclasses (used by raster, e055, e251, e142)
# -----------------------------------------------------------------------------


@dataclass
class WordBox:
    """Single word with bounding box and center (e.g. from Vision API)."""

    text: str
    cx: float
    cy: float
    bbox: Tuple[float, float, float, float]


@dataclass
class RowCluster:
    """Words grouped by row (same Y band)."""

    row_y: float
    words: List[WordBox]


# -----------------------------------------------------------------------------
# Text normalization
# -----------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """NFKC Unicode normalization."""
    return unicodedata.normalize("NFKC", text or "")


def compact_text(text: str) -> str:
    """Normalize and remove spaces (full-/half-width)."""
    return normalize_text(text).replace(" ", "").replace("　", "")


# -----------------------------------------------------------------------------
# Drawing number (raster + vector)
# -----------------------------------------------------------------------------

DRAWING_NO_PATTERN = re.compile(r"^[A-Z]{1,4}-[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8})*$")


def normalize_drawing_number_candidate(text: str) -> Optional[str]:
    """Normalize and validate drawing number pattern; return None if no match."""
    normalized = normalize_text(text).upper()
    normalized = normalized.replace(" ", "").replace("　", "")
    normalized = re.sub(r"[‐‑‒–—―ー−－]", "-", normalized)
    normalized = normalized.strip("|,:;[](){}<>「」『』")
    if DRAWING_NO_PATTERN.fullmatch(normalized):
        return normalized
    return None


# -----------------------------------------------------------------------------
# Row clustering and row text (raster, e055, e251, e142)
# -----------------------------------------------------------------------------


def cluster_by_y(words: List[WordBox], threshold: float) -> List[RowCluster]:
    """Group words into rows by Y proximity."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w.cy)
    clusters: List[RowCluster] = [
        RowCluster(row_y=sorted_words[0].cy, words=[sorted_words[0]])
    ]
    for word in sorted_words[1:]:
        last = clusters[-1]
        if abs(word.cy - last.row_y) <= threshold:
            last.words.append(word)
            n = len(last.words)
            last.row_y = ((last.row_y * (n - 1)) + word.cy) / n
        else:
            clusters.append(RowCluster(row_y=word.cy, words=[word]))
    return clusters


def row_text(cluster: RowCluster) -> str:
    """Concatenate word texts in reading order (left to right)."""
    return "".join(w.text for w in sorted(cluster.words, key=lambda x: x.cx))


def row_text_normalized(cluster: RowCluster) -> str:
    """Space-joined normalized word texts in reading order (e.g. for e251)."""
    return " ".join(
        normalize_text(w.text).strip()
        for w in sorted(cluster.words, key=lambda x: x.cx)
    )


def split_cluster_by_x_gap(
    cluster: RowCluster, max_gap: float
) -> List[RowCluster]:
    """Split a row cluster into sub-clusters where horizontal gap exceeds max_gap."""
    if not cluster.words:
        return []
    words = sorted(cluster.words, key=lambda w: w.cx)
    grouped: List[List[WordBox]] = [[words[0]]]
    prev = words[0]
    for word in words[1:]:
        gap = word.bbox[0] - prev.bbox[2]
        if gap > max_gap:
            grouped.append([word])
        else:
            grouped[-1].append(word)
        prev = word

    split_clusters: List[RowCluster] = []
    for group in grouped:
        split_clusters.append(
            RowCluster(
                row_y=sum(w.cy for w in group) / max(1, len(group)),
                words=group,
            )
        )
    return split_clusters
