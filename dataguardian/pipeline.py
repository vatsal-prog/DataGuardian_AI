"""Run the agent chain and publish only when validation passes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dataguardian.agents.ingestion import IngestionAgent
from dataguardian.agents.profiler import ProfilerAgent
from dataguardian.agents.query import QueryAgent
from dataguardian.agents.quality import QualityAgent
from dataguardian.agents.repair import RepairAgent
from dataguardian.agents.root_cause import RootCauseAgent
from dataguardian.agents.validation import ValidationAgent, score_issues
from dataguardian.cleaning import snake_case
from dataguardian.paths import SAMPLES_DIR
from dataguardian.pipeline_error import PipelineError
from dataguardian.serialize import frame_records, to_jsonable
from dataguardian.sources import SourceSpec
from dataguardian.storage import Warehouse

logger = logging.getLogger("dataguardian")

_SAMPLES = {
    "orders": SAMPLES_DIR / "orders.csv",
    "customers": SAMPLES_DIR / "customers.json",
    "broken_ledger": SAMPLES_DIR / "broken_ledger.csv",
}


class Pipeline:
    def __init__(self, warehouse: Warehouse):
        self.warehouse = warehouse
        self.ingestion = IngestionAgent()
        self.profiler = ProfilerAgent()
        self.quality = QualityAgent()
        self.root_cause = RootCauseAgent()
        self.repair = RepairAgent()
        self.validation = ValidationAgent()
        self.query = QueryAgent(warehouse)

    def run(self, spec: SourceSpec) -> dict:
        run_id = uuid.uuid4().hex[:12]
        created_at = _now()
        ingested = self.ingestion.run(spec)
        frame = ingested.pop("frame")
        profile = self.profiler.run(frame)
        issues = self.quality.run(frame, profile)
        causes = self.root_cause.run(frame, profile, issues)
        auto_plan = self.repair.plan_auto(frame, profile, issues)
        repaired, auto_applied = self.repair.apply(frame, auto_plan, include_review=False)
        post_profile = self.profiler.run(repaired)
        residual = self.quality.run(repaired, post_profile)
        review_plan = self.repair.plan_review(repaired, post_profile, residual)
        repairs = _renumber([*auto_applied, *review_plan])
        validation = self.validation.run(frame, repaired, residual, score_issues(issues))
        name = display_name(spec)

        report = {
            "run_id": run_id,
            "created_at": created_at,
            "status": "pass" if validation["passed"] else "fail",
            "decision": "store" if validation["passed"] else "rollback",
            "decision_detail": "",
            "display_name": name,
            "source": {"kind": spec.kind, "location": spec.location or spec.kind},
            "ingestion": ingested,
            "profile": profile,
            "issues": issues,
            "root_causes": causes,
            "repairs": repairs,
            "validation": validation,
            "preview_before": frame_records(frame),
            "preview_after": frame_records(repaired),
            "rows_before": int(len(frame)),
            "rows_after": int(len(repaired)),
            "dataset_id": None,
            "review_id": None,
        }

        if validation["passed"]:
            dataset = self.warehouse.publish(
                repaired,
                {
                    "display_name": name,
                    "source_kind": spec.kind,
                    "source_location": spec.location,
                    "quality_score": validation["score_after"],
                    "run_id": run_id,
                    "columns": [
                        {
                            "name": column["name"],
                            "inferred_type": column["inferred_type"],
                            "role": column["role"],
                        }
                        for column in post_profile["columns"]
                    ],
                },
            )
            report["dataset_id"] = dataset["id"]
            report["decision_detail"] = (
                "Validation passed. The repaired rows were stored in the warehouse. "
                "The query agent and the dashboard can use them."
            )
            logger.info("run %s passed; stored %s rows as %s", run_id, len(repaired), name)
        else:
            report["decision_detail"] = (
                "Validation failed, so nothing was published. Staged rows are waiting "
                "for a person to approve the review repairs or discard them."
            )
            review = self.warehouse.hold_for_review(run_id, frame, report)
            report["review_id"] = review["id"]
            self.warehouse.update_review_report(review["id"], report)
            logger.info("run %s failed validation; review %s opened", run_id, review["id"])

        self.warehouse.save_run(to_jsonable(report))
        return to_jsonable(report)

    def approve(self, review_id: str) -> dict:
        review = self.warehouse.get_review(review_id)
        if review is None:
            raise PipelineError("Review not found.")
        if review["status"] != "pending":
            raise PipelineError("This review is already resolved.")
        original = review["report"]
        frame = self.warehouse.read_staged(review["staged_path"])
        repaired, applied = self.repair.apply(frame, original.get("repairs") or [], include_review=True)
        profile = self.profiler.run(repaired)
        residual = self.quality.run(repaired, profile)
        validation = self.validation.run(
            frame,
            repaired,
            residual,
            (original.get("validation") or {}).get("score_before") or score_issues(original.get("issues") or []),
        )
        if not validation["passed"]:
            return to_jsonable(
                {
                    "status": "fail",
                    "decision": "rollback",
                    "review_id": review_id,
                    "dataset_id": None,
                    "validation": validation,
                    "repairs": applied,
                    "preview_after": frame_records(repaired),
                    "rows_after": int(len(repaired)),
                    "decision_detail": (
                        "The approved repairs still fail validation. The warehouse was left unchanged."
                    ),
                }
            )

        source = original.get("source") or {}
        dataset = self.warehouse.publish(
            repaired,
            {
                "display_name": original.get("display_name") or "dataset",
                "source_kind": source.get("kind"),
                "source_location": source.get("location"),
                "quality_score": validation["score_after"],
                "run_id": original.get("run_id"),
                "columns": [
                    {
                        "name": column["name"],
                        "inferred_type": column["inferred_type"],
                        "role": column["role"],
                    }
                    for column in profile["columns"]
                ],
            },
        )
        self.warehouse.resolve_review(review_id, "approved", dataset["id"])
        approved = {
            "run_id": uuid.uuid4().hex[:12],
            "created_at": _now(),
            "status": "pass",
            "decision": "store",
            "decision_detail": "A reviewer approved the repairs. Validation passed and the rows were stored.",
            "display_name": original.get("display_name"),
            "source": source,
            "ingestion": original.get("ingestion"),
            "profile": profile,
            "issues": original.get("issues"),
            "root_causes": original.get("root_causes"),
            "repairs": applied,
            "validation": validation,
            "preview_before": original.get("preview_before"),
            "preview_after": frame_records(repaired),
            "rows_before": original.get("rows_before"),
            "rows_after": int(len(repaired)),
            "dataset_id": dataset["id"],
            "review_id": review_id,
            "parent_run_id": original.get("run_id"),
        }
        self.warehouse.save_run(to_jsonable(approved))
        return to_jsonable(approved)

    def reject(self, review_id: str) -> dict:
        review = self.warehouse.resolve_review(review_id, "rejected", None)
        return {
            "id": review["id"],
            "status": review["status"],
            "decision": "rollback",
            "decision_detail": "The staged rows were discarded. The warehouse was not changed.",
        }

    def ask(self, question: str, dataset_id: str | None = None) -> dict:
        return self.query.ask(question, dataset_id)


def sample_spec(name: str) -> SourceSpec:
    path = _SAMPLES.get((name or "").lower())
    if path is None or not path.is_file():
        known = ", ".join(sorted(_SAMPLES))
        raise PipelineError(f"Unknown sample '{name}'. Choose one of: {known}.")
    kind = "json" if path.suffix == ".json" else "csv"
    return SourceSpec(kind=kind, location=str(path), display_name=path.stem)


def display_name(spec: SourceSpec) -> str:
    if spec.display_name:
        base = spec.display_name
    elif spec.kind in {"csv", "json", "sqlite"} and spec.location:
        base = Path(spec.location).stem
    elif spec.kind == "api":
        base = "api_feed"
    else:
        base = spec.kind or "dataset"
    return snake_case(base) or "dataset"


def _renumber(repairs: list[dict]) -> list[dict]:
    for index, repair in enumerate(repairs, start=1):
        repair["id"] = f"R{index:03d}"
    return repairs


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
