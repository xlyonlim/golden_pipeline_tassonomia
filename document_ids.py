"""Identificativi stabili dei documenti del dataset."""

from __future__ import annotations

import re
from pathlib import Path


VALID_PREFIXES = ("GOLD", "INPUT")
DOCUMENT_ID_RE = re.compile(r"^(GOLD|INPUT)_(\d+)$", re.IGNORECASE)
LEGACY_ID_RE = re.compile(r"^(?:ATTO_?)?(\d+)$", re.IGNORECASE)


def normalize_prefix(value: str) -> str:
    prefix = str(value).strip().upper()
    if prefix not in VALID_PREFIXES:
        raise ValueError(
            f"Prefisso identificativo non valido {value!r}; "
            f"valori ammessi: {', '.join(VALID_PREFIXES)}"
        )
    return prefix


def canonical_document_id(value: str, default_prefix: str | None = None) -> str:
    """Restituisce un ID ``GOLD_####`` o ``INPUT_####``.

    Gli identificativi legacy ``ATTO_N`` sono accettati solo quando il chiamante
    indica esplicitamente a quale insieme appartengono.
    """
    text = Path(str(value).strip()).stem.upper()
    match = DOCUMENT_ID_RE.fullmatch(text)
    if match:
        return f"{match.group(1).upper()}_{int(match.group(2)):04d}"

    legacy = LEGACY_ID_RE.fullmatch(text)
    if legacy and default_prefix is not None:
        prefix = normalize_prefix(default_prefix)
        return f"{prefix}_{int(legacy.group(1)):04d}"

    expected = "GOLD_0001 o INPUT_0001"
    if default_prefix is not None:
        expected += f" (sono ammessi anche gli ID legacy per {normalize_prefix(default_prefix)})"
    raise ValueError(f"Identificativo documento non valido {value!r}; atteso {expected}")


def document_id_parts(
    value: str, default_prefix: str | None = None
) -> tuple[str, int]:
    canonical = canonical_document_id(value, default_prefix=default_prefix)
    prefix, number = canonical.split("_", 1)
    return prefix, int(number)


def document_filename(value: str, default_prefix: str | None = None) -> str:
    return f"{canonical_document_id(value, default_prefix=default_prefix)}.pdf"


def document_sort_key(value: str) -> tuple[int, int, str]:
    """Ordina prima GOLD, poi INPUT, mantenendo un fallback per dati legacy."""
    try:
        prefix, number = document_id_parts(value)
        return (VALID_PREFIXES.index(prefix), number, str(value).lower())
    except ValueError:
        match = re.search(r"(\d+)$", Path(str(value)).stem)
        number = int(match.group(1)) if match else 10**9
        return (len(VALID_PREFIXES), number, str(value).lower())


def infer_dataset_prefix(input_root: Path) -> str:
    parts = {part.lower() for part in input_root.resolve().parts}
    return "GOLD" if any("golden" in part for part in parts) else "INPUT"
