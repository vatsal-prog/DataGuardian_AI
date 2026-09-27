"""Repair agent: plan fixes, and apply only the ones that are safe to automate."""

from __future__ import annotations

import pandas as pd

from dataguardian.cleaning import (
    canonical_case_map,
    country_canonical,
    is_blank,
    is_country_name,
    is_key_name,
    is_sentinel_token,
    is_text_series,
    parse_bool,
    parse_dates,
    parse_number,
)

STRATEGY_ORDER = [
    "trim_whitespace",
    "replace_sentinels",
    "coerce_numeric",
    "normalize_country",
    "canonicalize_case",
    "coerce_dates",
    "coerce_booleans",
    "drop_exact_duplicates",
    "absolute_value",
    "impute_median",
    "drop_incomplete_rows",
    "drop_outlier_rows",
    "dedupe_key",
]


class RepairAgent:
    def plan_auto(self, frame: pd.DataFrame, profile: dict, issues: list[dict]) -> list[dict]:
        repairs: list[dict] = []
        seen: set[tuple] = set()
        columns = {column["name"]: column for column in profile["columns"]}

        def add(**kwargs) -> None:
            key = (kwargs["strategy"], kwargs.get("column"))
            if key in seen:
                return
            seen.add(key)
            repairs.append(
                {
                    "id": f"R{len(repairs) + 1:03d}",
                    "issue_id": kwargs.get("issue_id"),
                    "column": kwargs.get("column"),
                    "strategy": kwargs["strategy"],
                    "description": kwargs["description"],
                    "disposition": kwargs["disposition"],
                    "applied": False,
                    "rows_affected": 0,
                    "target_type": kwargs.get("target_type"),
                }
            )

        for issue in issues:
            column = issue.get("column")
            issue_type = issue["issue_type"]
            profile_column = columns.get(column or "")
            if issue_type == "whitespace" and column:
                add(
                    strategy="trim_whitespace",
                    column=column,
                    issue_id=issue["id"],
                    disposition="auto",
                    description=f"Trim whitespace in {column}.",
                )
            elif issue_type == "sentinel_values" and column:
                add(
                    strategy="replace_sentinels",
                    column=column,
                    issue_id=issue["id"],
                    disposition="auto",
                    description=f"Turn placeholder tokens in {column} into empty values.",
                )
            elif issue_type in {"numeric_format", "non_numeric"} and column:
                add(
                    strategy="coerce_numeric",
                    column=column,
                    issue_id=issue["id"],
                    disposition="auto",
                    target_type=(profile_column or {}).get("inferred_type"),
                    description=f"Parse {column} as a number, removing currency symbols and thousands separators.",
                )
            elif issue_type == "inconsistent_categories" and column:
                if is_country_name(column):
                    add(
                        strategy="normalize_country",
                        column=column,
                        issue_id=issue["id"],
                        disposition="auto",
                        description=f"Map country aliases in {column} onto one canonical name.",
                    )
                add(
                    strategy="canonicalize_case",
                    column=column,
                    issue_id=issue["id"],
                    disposition="auto",
                    description=f"Collapse case variants in {column} to the most common spelling.",
                )
            elif issue_type in {"invalid_datetime", "date_format"} and column:
                add(
                    strategy="coerce_dates",
                    column=column,
                    issue_id=issue["id"],
                    disposition="auto",
                    description=f"Rewrite {column} as ISO dates. Values that are not dates become empty.",
                )
            elif issue_type == "exact_duplicates":
                add(
                    strategy="drop_exact_duplicates",
                    column=None,
                    issue_id=issue["id"],
                    disposition="auto",
                    description="Drop exact duplicate rows, keeping the first copy.",
                )

        for column in profile["columns"]:
            if column["inferred_type"] == "boolean":
                add(
                    strategy="coerce_booleans",
                    column=column["name"],
                    disposition="auto",
                    description=f"Normalize {column['name']} to true or false.",
                )
        return repairs

    def plan_review(self, frame: pd.DataFrame, profile: dict, issues: list[dict]) -> list[dict]:
        """Plan repairs for issues that remain after the automatic fixes."""
        repairs: list[dict] = []
        seen: set[tuple] = set()
        columns = {column["name"]: column for column in profile["columns"]}

        def add(**kwargs) -> None:
            key = (kwargs["strategy"], kwargs.get("column"))
            if key in seen:
                return
            seen.add(key)
            repairs.append(
                {
                    "id": "",
                    "issue_id": kwargs.get("issue_id"),
                    "column": kwargs.get("column"),
                    "strategy": kwargs["strategy"],
                    "description": kwargs["description"],
                    "disposition": kwargs["disposition"],
                    "applied": False,
                    "rows_affected": 0,
                    "target_type": kwargs.get("target_type"),
                }
            )

        for issue in issues:
            column = issue.get("column")
            severity = issue["severity"]
            profile_column = columns.get(column or "", {})
            role = profile_column.get("role")
            if issue["issue_type"] == "negative_values" and column and severity in {"high", "critical"}:
                add(
                    strategy="absolute_value",
                    column=column,
                    issue_id=issue["id"],
                    disposition="review",
                    description=f"Take the absolute value of negative {column} entries. Confirm these are sign errors, not refunds.",
                )
            elif issue["issue_type"] == "duplicate_key" and column:
                add(
                    strategy="dedupe_key",
                    column=column,
                    issue_id=issue["id"],
                    disposition="review",
                    description=f"Keep the first row for each repeated {column} and drop the later conflicts.",
                )
            elif issue["issue_type"] == "missing_values" and column and severity in {"high", "critical"}:
                if role in {"money", "quantity", "key"} or profile_column.get("inferred_type") in {"integer", "float"}:
                    add(
                        strategy="drop_incomplete_rows",
                        column=column,
                        issue_id=issue["id"],
                        disposition="review",
                        target_type=profile_column.get("inferred_type"),
                        description=f"Drop rows where {column} is still empty. Too many values are missing to impute safely.",
                    )
            elif (
                issue["issue_type"] == "missing_values"
                and column
                and severity in {"low", "medium"}
                and profile_column.get("inferred_type") in {"integer", "float"}
                and not is_key_name(column)
                and role != "key"
            ):
                null_pct = profile_column.get("null_pct") or 0
                if null_pct <= 0.2:
                    add(
                        strategy="impute_median",
                        column=column,
                        issue_id=issue["id"],
                        disposition="suggested",
                        description=f"Fill empty {column} cells with the median. Suggested only, because it invents values.",
                    )
            elif issue["issue_type"] == "outliers" and column and severity in {"high", "critical"}:
                add(
                    strategy="drop_outlier_rows",
                    column=column,
                    issue_id=issue["id"],
                    disposition="review",
                    description=f"Drop rows whose {column} is an extreme outlier. Review them before they are removed.",
                )

        return repairs

    def apply(self, frame: pd.DataFrame, repairs: list[dict], *, include_review: bool) -> tuple[pd.DataFrame, list[dict]]:
        selected = [repair for repair in repairs if _should_apply(repair, include_review)]
        selected.sort(
            key=lambda repair: STRATEGY_ORDER.index(repair["strategy"]) if repair["strategy"] in STRATEGY_ORDER else 100
        )
        running = frame.copy()
        applied_map: dict[tuple, dict] = {}
        for repair in selected:
            running, affected = _STRATEGIES[repair["strategy"]](running, repair)
            updated = dict(repair)
            updated["applied"] = True
            updated["rows_affected"] = int(affected)
            applied_map[(repair["strategy"], repair.get("column"))] = updated
        merged = []
        for repair in repairs:
            key = (repair["strategy"], repair.get("column"))
            if key in applied_map and _should_apply(repair, include_review):
                merged.append(applied_map.pop(key))
            else:
                merged.append(dict(repair))
        return running, merged


def _should_apply(repair: dict, include_review: bool) -> bool:
    if repair.get("disposition") == "auto":
        return True
    if repair.get("disposition") == "review" and include_review:
        return True
    return False


def _trim(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair.get("column")
    if column is None or column not in frame.columns:
        return frame, 0
    return _trim_column(frame, column)


def _trim_column(frame: pd.DataFrame, column: str) -> tuple[pd.DataFrame, int]:
    series = frame[column]
    if not is_text_series(series):
        return frame, 0
    stripped = series.map(lambda value: value if is_blank(value) else str(value).strip())
    changed = int((series.map(_label) != stripped.map(_label)).sum())
    frame[column] = stripped
    return frame, changed


def _sentinels(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    mask = frame[column].map(is_sentinel_token)
    frame.loc[mask, column] = pd.NA
    return frame, int(mask.sum())


def _coerce_numeric(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    parsed = []
    changed = 0
    for value in frame[column].tolist():
        if is_blank(value) or is_sentinel_token(value):
            parsed.append(pd.NA)
            continue
        number = parse_number(value)
        if number is None:
            parsed.append(pd.NA)
            changed += 1
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) == number:
            parsed.append(number)
            continue
        if str(value).strip() not in {str(number), str(int(number)) if number.is_integer() else str(number)}:
            changed += 1
        parsed.append(number)
    whole = all(value is pd.NA or float(value).is_integer() for value in parsed)
    target = repair.get("target_type")
    if target == "integer" and whole:
        frame[column] = pd.Series(parsed, index=frame.index, dtype="Int64")
    else:
        frame[column] = pd.Series(parsed, index=frame.index, dtype="Float64")
    return frame, changed


def _countries(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    changed = 0
    updated = []
    for value in frame[column].tolist():
        if is_blank(value) or is_sentinel_token(value):
            updated.append(value)
            continue
        text = str(value).strip()
        canonical = country_canonical(text)
        if canonical and canonical != text:
            changed += 1
            updated.append(canonical)
        elif text != str(value):
            changed += 1
            updated.append(text)
        else:
            updated.append(text)
    frame[column] = updated
    return frame, changed


def _case(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    mapping = canonical_case_map(frame[column])
    if not mapping:
        return frame, 0
    changed = 0
    updated = []
    for value in frame[column].tolist():
        if is_blank(value):
            updated.append(value)
            continue
        text = str(value).strip()
        replacement = mapping.get(text)
        if replacement and replacement != text:
            changed += 1
            updated.append(replacement)
        else:
            updated.append(text if text == str(value) or is_sentinel_token(value) else text)
    frame[column] = updated
    return frame, changed


def _dates(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    parsed = parse_dates(frame[column])
    changed = 0
    updated = []
    for value, stamp in zip(frame[column].tolist(), parsed.tolist()):
        if is_blank(value) or is_sentinel_token(value):
            updated.append(pd.NA)
            continue
        if pd.isna(stamp):
            updated.append(pd.NA)
            changed += 1
            continue
        iso = pd.Timestamp(stamp).strftime("%Y-%m-%d")
        if str(value).strip() != iso:
            changed += 1
        updated.append(iso)
    frame[column] = pd.Series(updated, index=frame.index, dtype="object")
    return frame, changed


def _booleans(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    changed = 0
    updated = []
    for value in frame[column].tolist():
        parsed = parse_bool(value)
        if parsed is None:
            updated.append(pd.NA if is_blank(value) or is_sentinel_token(value) else value)
            continue
        if value is not parsed:
            changed += 1
        updated.append(parsed)
    frame[column] = pd.Series(updated, index=frame.index, dtype="object")
    return frame, changed


def _dedup(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    before = len(frame)
    frame = frame.drop_duplicates(keep="first").reset_index(drop=True)
    return frame, before - len(frame)


def _absolute(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    changed = 0
    updated = []
    for value in frame[column].tolist():
        number = parse_number(value)
        if number is None:
            updated.append(value if not is_blank(value) else pd.NA)
            continue
        if number < 0:
            changed += 1
            updated.append(abs(number))
        else:
            updated.append(number)
    frame[column] = pd.Series(updated, index=frame.index, dtype="Float64")
    return frame, changed


def _impute(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    numbers = [number for value in frame[column].tolist() if (number := parse_number(value)) is not None]
    if not numbers:
        return frame, 0
    median = float(pd.Series(numbers).median())
    changed = 0
    updated = []
    for value in frame[column].tolist():
        if is_blank(value) or is_sentinel_token(value):
            changed += 1
            updated.append(median)
        else:
            number = parse_number(value)
            updated.append(median if number is None else number)
            if number is None:
                changed += 1
    frame[column] = pd.Series(updated, index=frame.index, dtype="Float64")
    return frame, changed


def _drop_incomplete(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    numeric = repair.get("target_type") in {"integer", "float"} or pd.api.types.is_numeric_dtype(frame[column])

    def incomplete(value: object) -> bool:
        if is_blank(value) or is_sentinel_token(value):
            return True
        if numeric and parse_number(value) is None:
            return True
        return False

    mask = frame[column].map(incomplete)
    changed = int(mask.sum())
    kept = frame.loc[~mask].reset_index(drop=True)
    return kept, changed


def _drop_outliers(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    numbers = pd.Series([parse_number(value) for value in frame[column].tolist()], dtype="float64")
    clean = numbers.dropna()
    if len(clean) < 8:
        return frame, 0
    q1 = float(clean.quantile(0.25))
    q3 = float(clean.quantile(0.75))
    iqr = q3 - q1
    if iqr == 0:
        return frame, 0
    low = q1 - 1.5 * iqr
    high = q3 + 1.5 * iqr
    mask = numbers.map(lambda number: pd.notna(number) and (number < low or number > high))
    changed = int(mask.sum())
    kept = frame.loc[~mask.fillna(False)].reset_index(drop=True)
    return kept, changed


def _dedupe_key(frame: pd.DataFrame, repair: dict) -> tuple[pd.DataFrame, int]:
    column = repair["column"]
    before = len(frame)
    frame = frame.drop_duplicates(subset=[column], keep="first").reset_index(drop=True)
    return frame, before - len(frame)


def _label(value: object) -> str:
    if is_blank(value):
        return ""
    return str(value)


_STRATEGIES = {
    "trim_whitespace": _trim,
    "replace_sentinels": _sentinels,
    "coerce_numeric": _coerce_numeric,
    "normalize_country": _countries,
    "canonicalize_case": _case,
    "coerce_dates": _dates,
    "coerce_booleans": _booleans,
    "drop_exact_duplicates": _dedup,
    "absolute_value": _absolute,
    "impute_median": _impute,
    "drop_incomplete_rows": _drop_incomplete,
    "drop_outlier_rows": _drop_outliers,
    "dedupe_key": _dedupe_key,
}
