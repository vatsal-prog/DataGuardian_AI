"""Validation agent: decide whether the repaired dataset is safe to publish."""

from __future__ import annotations

import pandas as pd

PASS_SCORE = 70
WEIGHTS = {"critical": 30, "high": 15, "medium": 5, "low": 1}


class ValidationAgent:
    def run(
        self,
        before: pd.DataFrame,
        after: pd.DataFrame,
        residual_issues: list[dict],
        score_before: int,
    ) -> dict:
        score_after = score_issues(residual_issues)
        rows_before = int(len(before))
        rows_after = int(len(after))
        critical = [issue for issue in residual_issues if issue["severity"] == "critical"]
        high = [issue for issue in residual_issues if issue["severity"] == "high"]
        checks = [
            {
                "name": "non_empty",
                "passed": rows_after > 0 and len(after.columns) > 0,
                "detail": f"{rows_after} rows and {len(after.columns)} columns remain.",
            },
            {
                "name": "rows_retained",
                "passed": rows_before == 0 or rows_after >= rows_before * 0.5,
                "detail": f"{rows_after} of {rows_before} rows retained.",
            },
            {
                "name": "no_critical_issues",
                "passed": not critical,
                "detail": "No critical issues remain." if not critical else f"{len(critical)} critical issue(s) remain.",
            },
            {
                "name": "no_high_issues",
                "passed": not high,
                "detail": "No high-severity issues remain." if not high else f"{len(high)} high-severity issue(s) remain.",
            },
            {
                "name": "quality_score",
                "passed": score_after >= PASS_SCORE,
                "detail": f"Score is {score_after}. The publish bar is {PASS_SCORE}.",
            },
        ]
        return {
            "passed": all(check["passed"] for check in checks),
            "score_before": score_before,
            "score_after": score_after,
            "pass_score": PASS_SCORE,
            "checks": checks,
            "residual_issues": residual_issues,
        }


def score_issues(issues: list[dict]) -> int:
    penalty = sum(WEIGHTS.get(issue["severity"], 0) for issue in issues)
    return max(0, 100 - penalty)
