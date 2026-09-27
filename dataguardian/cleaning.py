"""Shared type, token, and column-role rules used by every agent."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

SENTINELS = {
    "n/a",
    "na",
    "null",
    "none",
    "-",
    "--",
    "nan",
    "?",
    "tbd",
    "undefined",
    "nil",
    "n.a.",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "united kingdom": "United Kingdom",
    "uae": "United Arab Emirates",
    "u.a.e.": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates",
}

_KEY_RE = re.compile(r"(^|_)(id|uuid)$", re.IGNORECASE)


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def is_sentinel_token(value: object) -> bool:
    if is_blank(value) or isinstance(value, (bool, int, float, np.integer, np.floating)):
        return False
    return str(value).strip().casefold() in SENTINELS


def is_missing(value: object) -> bool:
    return is_blank(value) or is_sentinel_token(value)


def snake_case(name: str) -> str:
    text = str(name).strip().lower()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "column"
    if text[0].isdigit():
        text = f"c_{text}"
    return text


def parse_number(value: object) -> float | None:
    if isinstance(value, bool) or is_blank(value) or is_sentinel_token(value):
        return None
    if isinstance(value, (int, np.integer)):
        return float(value)
    if isinstance(value, (float, np.floating)):
        if math.isnan(float(value)):
            return None
        return float(value)
    text = str(value).strip()
    if text[:1] in "$€£":
        text = text[1:].strip()
    text = text.replace(",", "")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", text):
        return float(text)
    return None


def has_numeric_decoration(value: object) -> bool:
    if isinstance(value, (bool, int, float, np.integer, np.floating)) or is_blank(value):
        return False
    text = str(value)
    return any(symbol in text for symbol in "$€£,") and parse_number(value) is not None


def parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return bool(value)
    if is_blank(value) or is_sentinel_token(value):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return None
    token = str(value).strip().casefold()
    if token in {"true", "t", "yes", "y"}:
        return True
    if token in {"false", "f", "no", "n"}:
        return False
    return None


def looks_like_email(value: object) -> bool:
    if is_blank(value) or is_sentinel_token(value):
        return False
    return EMAIL_RE.match(str(value).strip()) is not None


def is_key_name(name: str) -> bool:
    return bool(_KEY_RE.search(name)) or name.lower() in {"id", "uuid"}


def is_country_name(name: str) -> bool:
    lowered = name.lower()
    return "country" in lowered or "nation" in lowered


def column_role(name: str, inferred_type: str) -> str:
    lowered = name.lower()
    if is_key_name(lowered):
        return "key"
    if any(token in lowered for token in ("amount", "price", "cost", "revenue", "balance", "fee", "total")):
        return "money"
    if any(token in lowered for token in ("qty", "quantity", "units")):
        return "quantity"
    if inferred_type == "email" or "email" in lowered:
        return "email"
    if inferred_type == "datetime" or lowered.endswith(("_date", "_at")) or "date" in lowered:
        return "date"
    if any(token in lowered for token in ("note", "comment", "memo", "description")):
        return "free_text"
    return "default"


def severity_for_ratio(ratio: float, bands: list[tuple[float, str]]) -> str | None:
    """Pick the severity whose minimum ratio is the highest threshold still met."""
    if ratio <= 0 or not bands:
        return None
    chosen = None
    for threshold, level in sorted(bands, key=lambda item: item[0]):
        if ratio >= threshold:
            chosen = level
    return chosen


def missing_bands(role: str) -> list[tuple[float, str]]:
    table = {
        "key": [(0.0, "high"), (0.1, "critical")],
        "money": [(0.0, "low"), (0.02, "medium"), (0.15, "high"), (0.5, "critical")],
        "quantity": [(0.0, "low"), (0.02, "medium"), (0.15, "high"), (0.5, "critical")],
        "date": [(0.0, "low"), (0.05, "medium"), (0.25, "high"), (0.5, "critical")],
        "email": [(0.0, "low"), (0.08, "medium"), (0.3, "high"), (0.6, "critical")],
        "default": [(0.0, "low"), (0.1, "medium"), (0.35, "high"), (0.6, "critical")],
        "free_text": [],
    }
    return table.get(role, table["default"])


def non_missing_values(series: pd.Series) -> list[object]:
    return [value for value in series.tolist() if not is_blank(value) and not is_sentinel_token(value)]


def canonical_case_map(series: pd.Series) -> dict[str, str]:
    """Map stripped case-variants onto the most common spelling."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for value in series.tolist():
        if is_blank(value) or is_sentinel_token(value):
            continue
        text = str(value).strip()
        grouped[text.casefold()].append(text)
    mapping: dict[str, str] = {}
    for variants in grouped.values():
        counts = Counter(variants)
        if len(counts) < 2:
            continue
        winner = sorted(counts, key=lambda item: (-counts[item], -len(item), item))[0]
        for variant in counts:
            if variant != winner:
                mapping[variant] = winner
    return mapping


def country_canonical(value: object) -> str | None:
    if is_blank(value) or is_sentinel_token(value):
        return None
    text = str(value).strip()
    return COUNTRY_ALIASES.get(text.casefold())


def parse_dates(values: pd.Series | list) -> pd.Series:
    series = values if isinstance(values, pd.Series) else pd.Series(values, dtype="object")
    try:
        return pd.to_datetime(series, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return pd.to_datetime(series, errors="coerce")


def histogram(values: pd.Series, bins: int = 8) -> list[dict[str, float | int]]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return []
    if int(clean.nunique()) == 1:
        number = float(clean.iloc[0])
        return [{"lo": number, "hi": number, "count": int(len(clean))}]
    counts, edges = np.histogram(clean.to_numpy(dtype=float), bins=min(bins, max(int(clean.nunique()), 1)))
    buckets = []
    for index, count in enumerate(counts):
        buckets.append(
            {
                "lo": round(float(edges[index]), 4),
                "hi": round(float(edges[index + 1]), 4),
                "count": int(count),
            }
        )
    return buckets


def is_text_series(series: pd.Series) -> bool:
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)
