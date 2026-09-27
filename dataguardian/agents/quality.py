"""Quality agent: find concrete data issues and where they sit."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from dataguardian.cleaning import (
    canonical_case_map,
    country_canonical,
    is_blank,
    parse_dates,
    is_country_name,
    is_key_name,
    is_sentinel_token,
    looks_like_email,
    missing_bands,
    parse_number,
    severity_for_ratio,
)


class QualityAgent:
    def run(self, frame: pd.DataFrame, profile: dict) -> list[dict]:
        issues: list[dict] = []
        by_name = {column["name"]: column for column in profile["columns"]}

        duplicate_extras = int(frame.duplicated().sum())
        if duplicate_extras:
            mask = frame.duplicated(keep=False)
            ratio = duplicate_extras / max(len(frame), 1)
            severity = "critical" if ratio > 0.25 else "high"
            issues.append(
                _issue(
                    None,
                    severity,
                    "exact_duplicates",
                    f"{duplicate_extras} extra rows are exact copies of another row.",
                    mask,
                    {"extra_rows": duplicate_extras},
                )
            )

        for name in frame.columns:
            column = by_name[name]
            series = frame[name]
            self._column_issues(issues, name, series, column, len(frame))

        return refine_duplicate_keys(frame, issues)

    def _column_issues(
        self,
        issues: list[dict],
        name: str,
        series: pd.Series,
        column: dict,
        row_count: int,
    ) -> None:
        values = series.tolist()
        role = column["role"]
        inferred = column["inferred_type"]

        blank_mask = np.array([is_blank(value) for value in values])
        sentinel_mask = np.array([is_sentinel_token(value) for value in values])
        self._maybe_ratio_issue(
            issues,
            name,
            blank_mask,
            row_count,
            missing_bands(role),
            "missing_values",
            f"{int(blank_mask.sum())} values in {name} are empty.",
        )
        if sentinel_mask.any():
            tokens = sorted({str(value).strip() for value, flag in zip(values, sentinel_mask) if flag})
            issues.append(
                _issue(
                    name,
                    "medium" if sentinel_mask.mean() >= 0.05 else "low",
                    "sentinel_values",
                    f"{int(sentinel_mask.sum())} values in {name} use placeholder tokens such as {', '.join(tokens[:4])}.",
                    sentinel_mask,
                    {"tokens": tokens[:8]},
                )
            )

        if inferred in {"integer", "float"}:
            self._numeric_issues(issues, name, values, role, row_count)
        if inferred == "email" or role == "email":
            invalid = np.array(
                [
                    not is_blank(value)
                    and not is_sentinel_token(value)
                    and not looks_like_email(value)
                    for value in values
                ]
            )
            self._maybe_ratio_issue(
                issues,
                name,
                invalid,
                row_count,
                [(0.0, "medium"), (0.15, "high")],
                "invalid_email",
                f"{int(invalid.sum())} values in {name} are not email addresses.",
            )
        if inferred == "datetime" or role == "date":
            self._date_issues(issues, name, series, row_count)

        if _should_check_categories(name, inferred, values):
            self._category_issues(issues, name, values)

        whitespace = np.array(
            [
                not is_blank(value)
                and not isinstance(value, (int, float, bool))
                and str(value) != str(value).strip()
                for value in values
            ]
        )
        if whitespace.any():
            issues.append(
                _issue(
                    name,
                    "low",
                    "whitespace",
                    f"{int(whitespace.sum())} values in {name} have leading or trailing spaces.",
                    whitespace,
                    {},
                )
            )

        if is_key_name(name):
            self._duplicate_keys(issues, name, series, row_count)

    def _numeric_issues(self, issues: list[dict], name: str, values: list, role: str, row_count: int) -> None:
        decorated = np.array([has_decoration(value) for value in values])
        non_numeric = np.array(
            [
                not is_blank(value) and not is_sentinel_token(value) and parse_number(value) is None
                for value in values
            ]
        )
        negatives = np.array(
            [
                (number := parse_number(value)) is not None and number < 0
                for value in values
            ]
        )
        if decorated.any():
            issues.append(
                _issue(
                    name,
                    "medium",
                    "numeric_format",
                    f"{int(decorated.sum())} values in {name} include currency symbols or thousands separators.",
                    decorated,
                    {"examples": _examples(values, decorated)},
                )
            )
        if non_numeric.any():
            ratio = float(non_numeric.mean())
            severity = severity_for_ratio(ratio, [(0.0, "medium"), (0.05, "high"), (0.3, "critical")])
            issues.append(
                _issue(
                    name,
                    severity or "medium",
                    "non_numeric",
                    f"{int(non_numeric.sum())} values in {name} cannot be read as numbers.",
                    non_numeric,
                    {"examples": _examples(values, non_numeric)},
                )
            )
        if negatives.any():
            severity = "high" if role in {"money", "quantity"} else "medium"
            issues.append(
                _issue(
                    name,
                    severity,
                    "negative_values",
                    f"{int(negatives.sum())} values in {name} are negative.",
                    negatives,
                    {"examples": _examples(values, negatives)},
                )
            )
        self._outliers(issues, name, values)

    def _outliers(self, issues: list[dict], name: str, values: list) -> None:
        parsed = [(index, number) for index, value in enumerate(values) if (number := parse_number(value)) is not None]
        if len(parsed) < 8:
            return
        series = pd.Series([number for _, number in parsed])
        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        if iqr == 0:
            return
        low = q1 - 3 * iqr
        high = q3 + 3 * iqr
        median = float(series.median())
        flags = []
        unit_scale = False
        for index, number in parsed:
            if low <= number <= high:
                continue
            far = abs(number) > max(10 * abs(median), 1) if median else abs(number) > 1000
            if not far:
                continue
            flags.append(index)
            if median and abs(number) > max(20 * abs(median), 1):
                unit_scale = True
        if not flags:
            return
        mask = np.zeros(len(values), dtype=bool)
        mask[flags] = True
        issues.append(
            _issue(
                name,
                "high" if unit_scale else "medium",
                "outliers",
                f"{len(flags)} values in {name} sit outside the typical range.",
                mask,
                {"unit_scale": unit_scale, "low": round(low, 4), "high": round(high, 4)},
            )
        )

    def _date_issues(self, issues: list[dict], name: str, series: pd.Series, row_count: int) -> None:
        cleaned = [
            None if is_blank(value) or is_sentinel_token(value) else str(value).strip() for value in series.tolist()
        ]
        parsed = parse_dates(cleaned)
        invalid = []
        non_iso = []
        for value, stamp in zip(cleaned, parsed.tolist()):
            if value is None or is_blank(value):
                invalid.append(False)
                non_iso.append(False)
                continue
            if pd.isna(stamp):
                invalid.append(True)
                non_iso.append(False)
                continue
            invalid.append(False)
            iso = pd.Timestamp(stamp).strftime("%Y-%m-%d")
            non_iso.append(value != iso)
        invalid_mask = np.array(invalid)
        non_iso_mask = np.array(non_iso)
        self._maybe_ratio_issue(
            issues,
            name,
            invalid_mask,
            row_count,
            [(0.0, "medium"), (0.15, "high"), (0.4, "critical")],
            "invalid_datetime",
            f"{int(invalid_mask.sum())} values in {name} are not dates.",
        )
        if non_iso_mask.any() and not invalid_mask.any():
            issues.append(
                _issue(
                    name,
                    "low",
                    "date_format",
                    f"{int(non_iso_mask.sum())} dates in {name} are not in ISO format.",
                    non_iso_mask,
                    {},
                )
            )
        elif non_iso_mask.any():
            issues.append(
                _issue(
                    name,
                    "low",
                    "date_format",
                    f"{int(non_iso_mask.sum())} dates in {name} use a non-ISO format.",
                    non_iso_mask,
                    {},
                )
            )

    def _category_issues(self, issues: list[dict], name: str, values: list) -> None:
        case_map = canonical_case_map(pd.Series(values))
        forms = sorted(set(case_map) | set(case_map.values()))
        alias_groups: dict[str, set[str]] = defaultdict(set)
        if is_country_name(name):
            for value in values:
                canonical = country_canonical(value)
                if canonical is None or is_blank(value):
                    continue
                alias_groups[canonical].add(str(value).strip())
        alias_examples = [sorted(group) for group in alias_groups.values() if len(group) > 1]
        if not forms and not alias_examples:
            return
        examples = forms[:6]
        for group in alias_examples:
            for item in group:
                if item not in examples:
                    examples.append(item)
        issues.append(
            _issue(
                name,
                "medium",
                "inconsistent_categories",
                f"{name} uses more than one spelling for the same value.",
                np.array(
                    [
                        str(value).strip() in case_map
                        or (
                            country_canonical(value) is not None
                            and str(value).strip() != country_canonical(value)
                        )
                        for value in values
                    ]
                ),
                {"examples": examples[:8]},
            )
        )

    def _duplicate_keys(self, issues: list[dict], name: str, series: pd.Series, row_count: int) -> None:
        present = series.map(lambda value: not is_blank(value) and not is_sentinel_token(value))
        conflicting = np.zeros(len(series), dtype=bool)
        frame = pd.DataFrame({"key": series, "present": present})
        # Compare full rows later by the caller; here we only have the column.
        # Conflicting keys are computed by the pipeline helper below when the
        # full frame is available. This method handles the column-only signal
        # of repeated keys, then `_mark_conflicts` refines it.
        duplicated = series.duplicated(keep=False) & present
        if not bool(duplicated.any()):
            return
        # Placeholder; replaced by refine_duplicate_keys().
        conflicting[duplicated.to_numpy()] = True
        issues.append(
            _issue(
                name,
                "high",
                "duplicate_key",
                f"{name} is repeated on rows that are not exact copies.",
                conflicting,
                {"pending_conflict_check": True},
            )
        )

    def _maybe_ratio_issue(
        self,
        issues: list[dict],
        name: str | None,
        mask: np.ndarray,
        row_count: int,
        bands: list[tuple[float, str]],
        issue_type: str,
        description: str,
    ) -> None:
        if row_count == 0 or not mask.any():
            return
        severity = severity_for_ratio(float(mask.sum()) / row_count, bands)
        if severity is None:
            return
        issues.append(_issue(name, severity, issue_type, description, mask, {}))


def refine_duplicate_keys(frame: pd.DataFrame, issues: list[dict]) -> list[dict]:
    """Drop duplicate-key findings that are only exact row copies."""
    kept: list[dict] = []
    for issue in issues:
        if issue["issue_type"] != "duplicate_key" or not issue.get("column"):
            kept.append(issue)
            continue
        column = issue["column"]
        subset = frame[frame[column].duplicated(keep=False)].copy()
        conflict_indexes: list[int] = []
        for _, group in subset.groupby(subset[column].map(_group_token), dropna=False):
            if group.empty:
                continue
            if int(group.duplicated().sum()) + 1 < len(group) or len(group.drop_duplicates()) > 1:
                if len(group.drop_duplicates()) > 1:
                    conflict_indexes.extend(int(index) for index in group.index)
        if not conflict_indexes:
            continue
        mask = np.zeros(len(frame), dtype=bool)
        mask[conflict_indexes] = True
        ratio = len(set(conflict_indexes)) / max(len(frame), 1)
        issue = dict(issue)
        issue["severity"] = "critical" if ratio > 0.2 else "high"
        issue["row_count"] = int(mask.sum())
        issue["row_indices"] = [int(index) for index in np.flatnonzero(mask)[:40]]
        issue["evidence"] = {"groups": int(pd.Series(conflict_indexes).nunique() and len(set(conflict_indexes)))}
        issue["description"] = (
            f"{issue['row_count']} rows reuse {column} with different values elsewhere on the row."
        )
        kept.append(issue)
    for index, issue in enumerate(kept, start=1):
        issue["id"] = f"Q{index:03d}"
    return kept


def _should_check_categories(name: str, inferred: str, values: list) -> bool:
    if inferred == "boolean":
        return False
    if is_country_name(name) or inferred == "categorical":
        return True
    present = [value for value in values if not is_blank(value) and not is_sentinel_token(value)]
    if not present:
        return False
    unique = len({str(value).strip().casefold() for value in present})
    return unique <= 12 and unique / len(present) <= 0.5


def _issue(
    column: str | None,
    severity: str,
    issue_type: str,
    description: str,
    mask: np.ndarray,
    evidence: dict,
) -> dict:
    positions = [int(index) for index in np.flatnonzero(mask)[:40]]
    tail_ratio = 0.0
    if len(mask) and mask.any():
        indexes = np.flatnonzero(mask)
        tail_ratio = float((indexes >= len(mask) * 0.8).mean())
    payload = dict(evidence)
    payload["tail_ratio"] = round(tail_ratio, 4)
    return {
        "id": "",
        "column": column,
        "severity": severity,
        "issue_type": issue_type,
        "description": description,
        "row_count": int(mask.sum()),
        "row_indices": positions,
        "evidence": payload,
    }


def _examples(values: list, mask: np.ndarray, limit: int = 5) -> list[str]:
    found = []
    for value, flag in zip(values, mask):
        if not flag:
            continue
        found.append(str(value)[:60])
        if len(found) == limit:
            break
    return found


def has_decoration(value: object) -> bool:
    from dataguardian.cleaning import has_numeric_decoration

    return has_numeric_decoration(value)


def _group_token(value: object) -> str:
    if is_blank(value):
        return ""
    return str(value).strip()
