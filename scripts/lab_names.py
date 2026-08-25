"""Shared normalization for lab names used by ticket automations."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any


DEFAULT_LABS = ("Gozzi", "Iurilli", "Lombardo", "Rossi")


def clean_lab_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_lab_key(value: Any) -> str:
    """Return a stable ASCII key, ignoring case and an optional `Lab` suffix."""

    cleaned = clean_lab_name(value)
    if not cleaned:
        return ""

    ascii_value = (
        unicodedata.normalize("NFKD", cleaned)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    parts = [part for part in re.sub(r"[^a-z0-9]+", " ", ascii_value).split() if part]
    if parts and parts[-1] == "lab":
        parts.pop()
    return "".join(parts)


def canonical_lab_name(value: Any, known_labs: Iterable[str] = DEFAULT_LABS) -> str:
    cleaned = clean_lab_name(value)
    if not cleaned:
        return ""

    aliases = {normalize_lab_key(lab): clean_lab_name(lab) for lab in known_labs}
    return aliases.get(normalize_lab_key(cleaned), cleaned)
