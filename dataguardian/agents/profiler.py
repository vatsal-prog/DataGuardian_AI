"""Data profiler: infer types, statistics, and distributions."""

from __future__ import annotations

import pandas as pd

from dataguardian.cleaning import (
    column_role,
    histogram,
    is_blank,
    is_country_name,
    is_missing,
    is_sentinel_token,
    looks_like_email,
    parse_bool,
    parse_dates,
    parse_number,
)


class ProfilerAgent:
    def run(self, frame: pd.DataFrame) -> dict:
        columns = [self._column(frame[name], str(name)) for name in frame.columns]
        return {
            "row_count": int(len(frame)),
            "column_count": int(len(frame.columns)),
            "duplicate_row_count": int(frame.duplicated().sum()),
            "columns": columns,
        }

    def _column(self, series: pd.Series, name: str) -> dict:
        values = series.tolist()
        blank = sum(1 for value in values if is_blank(value))
        sentinels = sum(1 for value in values if is_sentinel_token(value))
        present = [value for value in values if not is_blank(value) and not is_sentinel_token(value)]
        inferred = _infer_type(name, present)
        unique = len({_token(value) for value in present})
        total = max(len(values), 1)
        profile = {
            "name": name,
            "inferred_type": inferred,
            "role": column_role(name, inferred),
            "null_count": int(blank),
            "null_pct": round(blank / total, 4),
            "sentinel_count": int(sentinels),
            "sentinel_pct": round(sentinels / total, 4),
            "unique_count": int(unique),
            "examples": [str(value)[:80] for value in present[:3]],
            "stats": None,
            "histogram": [],
            "top_values": [],
        }
        if inferred in {"integer", "float"}:
            numbers = pd.Series([parse_number(value) for value in present], dtype="float64").dropna()
            profile["stats"] = _numeric_stats(numbers)
            profile["histogram"] = histogram(numbers)
        else:
            profile["top_values"] = _top_values(present)
            if inferred == "boolean":
                profile["top_values"] = _top_values([parse_bool(value) for value in present])
        if is_country_name(name):
            profile["role"] = "country" if profile["role"] == "default" else profile["role"]
        return profile


def _infer_type(name: str, present: list[object]) -> str:
    if not present:
        return "unknown"
    total = len(present)
    numbers = [parse_number(value) for value in present]
    numeric_hits = [number for number in numbers if number is not None]
    if len(numeric_hits) / total >= 0.8:
        whole = all(abs(number - round(number)) < 1e-9 for number in numeric_hits)
        return "integer" if whole else "float"

    bool_hits = [parse_bool(value) for value in present]
    if sum(item is not None for item in bool_hits) / total >= 0.9:
        return "boolean"

    email_hits = sum(1 for value in present if looks_like_email(value))
    if "email" in name.lower() and email_hits / total >= 0.3:
        return "email"
    if email_hits / total >= 0.8:
        return "email"

    parsed_dates = parse_dates(present)
    if float(parsed_dates.notna().mean()) >= 0.8:
        return "datetime"

    unique = len({_token(value) for value in present})
    if unique <= 12 and unique / total <= 0.5:
        return "categorical"
    return "string"


def _numeric_stats(numbers: pd.Series) -> dict | None:
    if numbers.empty:
        return None
    stdev = float(numbers.std(ddof=1)) if len(numbers) > 1 else 0.0
    return {
        "min": _round(numbers.min()),
        "max": _round(numbers.max()),
        "mean": _round(numbers.mean()),
        "median": _round(numbers.median()),
        "stdev": _round(stdev),
        "p25": _round(numbers.quantile(0.25)),
        "p75": _round(numbers.quantile(0.75)),
    }


def _top_values(present: list[object], limit: int = 8) -> list[dict]:
    counts: dict[str, int] = {}
    for value in present:
        if is_missing(value):
            continue
        label = str(value).strip()
        counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [{"value": label, "count": count} for label, count in ranked]


def _token(value: object) -> str:
    return str(value).strip().casefold()


def _round(value: object) -> float:
    return round(float(value), 4)
