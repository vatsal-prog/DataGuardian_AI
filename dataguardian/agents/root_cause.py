"""Root-cause agent: explain why each quality issue is there."""

from __future__ import annotations

import pandas as pd


class RootCauseAgent:
    def run(self, frame: pd.DataFrame, profile: dict, issues: list[dict]) -> list[dict]:
        causes = []
        for index, issue in enumerate(issues, start=1):
            cause = _explain(issue, frame)
            cause["id"] = f"C{index:03d}"
            cause["issue_id"] = issue["id"]
            causes.append(cause)
        return causes


def _explain(issue: dict, frame: pd.DataFrame) -> dict:
    issue_type = issue["issue_type"]
    column = issue.get("column")
    evidence = issue.get("evidence") or {}
    handler = _HANDLERS.get(issue_type, _generic)
    category, confidence, summary, details = handler(issue, column, evidence, frame)
    return {
        "id": "",
        "issue_id": issue["id"],
        "column": column,
        "category": category,
        "confidence": confidence,
        "summary": summary,
        "evidence": details,
    }


def _sentinel(issue, column, evidence, frame):
    tokens = ", ".join(evidence.get("tokens") or []) or "placeholder tokens"
    return (
        "sentinel_encoding",
        0.9,
        f"Missing values in {column} were exported as sentinel tokens ({tokens}) instead of empty fields.",
        [f"{issue['row_count']} cells use those tokens."],
    )


def _numeric_format(issue, column, evidence, frame):
    examples = ", ".join(evidence.get("examples") or [])
    detail = f"Examples: {examples}." if examples else f"{issue['row_count']} decorated numbers."
    return (
        "display_formatting",
        0.92,
        f"{column} was formatted for display, with currency symbols or thousands separators, before it was exported.",
        [detail],
    )


def _non_numeric(issue, column, evidence, frame):
    examples = ", ".join(evidence.get("examples") or []) or "non-numeric text"
    return (
        "invalid_entry",
        0.74,
        f"Some {column} values are free text ({examples}) rather than numbers, so the measure cannot be aggregated safely.",
        [f"{issue['row_count']} cells failed numeric parsing."],
    )


def _duplicates(issue, column, evidence, frame):
    return (
        "ingestion_replay",
        0.86,
        "The same rows appear more than once. The extract was likely appended or replayed without a key check.",
        [f"{evidence.get('extra_rows', issue['row_count'])} extra copies are exact matches."],
    )


def _duplicate_key(issue, column, evidence, frame):
    return (
        "conflicting_keys",
        0.82,
        f"{column} is used as an identifier, but different rows share it. The source key is not unique, or two records were merged under one id.",
        [issue["description"]],
    )


def _whitespace(issue, column, evidence, frame):
    return (
        "export_formatting",
        0.84,
        f"Values in {column} were entered or exported without trimming.",
        [f"{issue['row_count']} cells have leading or trailing spaces."],
    )


def _categories(issue, column, evidence, frame):
    examples = ", ".join(evidence.get("examples") or [])
    detail = f"Observed forms: {examples}." if examples else issue["description"]
    return (
        "inconsistent_entry",
        0.8,
        f"{column} was filled by more than one convention, so the same category appears under different spellings.",
        [detail],
    )


def _dates(issue, column, evidence, frame):
    return (
        "mixed_date_formats",
        0.8,
        f"{column} mixes date formats or contains values that are not dates. Locale differences or unchecked text entry are the likely source.",
        [issue["description"]],
    )


def _date_format(issue, column, evidence, frame):
    return (
        "mixed_date_formats",
        0.77,
        f"{column} dates are real dates stored in more than one written format.",
        [issue["description"]],
    )


def _negative(issue, column, evidence, frame):
    examples = ", ".join(evidence.get("examples") or [])
    return (
        "sign_convention",
        0.7,
        f"Negative {column} values may be refunds, reversals, or sign errors. They should not be rewritten without a review.",
        [f"Examples: {examples}." if examples else issue["description"]],
    )


def _missing(issue, column, evidence, frame):
    tail = float(evidence.get("tail_ratio") or 0)
    if tail >= 0.7:
        return (
            "truncated_extract",
            0.72,
            f"Empty {column} values cluster at the end of the extract. A column may have been added late, or the export was cut off.",
            [f"{tail:.0%} of the empty cells are in the last fifth of the file."],
        )
    return (
        "missingness",
        0.58,
        f"{column} was left empty on {issue['row_count']} rows. The field was optional, skipped, or dropped upstream.",
        [issue["description"]],
    )


def _outliers(issue, column, evidence, frame):
    if evidence.get("unit_scale"):
        return (
            "unit_error",
            0.74,
            f"At least one {column} value is orders of magnitude away from the typical range, which often means a unit or scale mistake.",
            [issue["description"]],
        )
    return (
        "outlier",
        0.48,
        f"{column} has values outside the interquartile range. They may be rare real events or entry mistakes.",
        [issue["description"]],
    )


def _email(issue, column, evidence, frame):
    return (
        "invalid_entry",
        0.8,
        f"{column} contains values that are not email addresses. The source accepted free text without a format check.",
        [issue["description"]],
    )


def _generic(issue, column, evidence, frame):
    target = column or "the dataset"
    return (
        "unspecified",
        0.4,
        f"The issue in {target} does not match a more specific pattern.",
        [issue["description"]],
    )


_HANDLERS = {
    "sentinel_values": _sentinel,
    "numeric_format": _numeric_format,
    "non_numeric": _non_numeric,
    "exact_duplicates": _duplicates,
    "duplicate_key": _duplicate_key,
    "whitespace": _whitespace,
    "inconsistent_categories": _categories,
    "invalid_datetime": _dates,
    "date_format": _date_format,
    "negative_values": _negative,
    "missing_values": _missing,
    "outliers": _outliers,
    "invalid_email": _email,
}
