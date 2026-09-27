"""Warehouse catalog, published tables, run history, and the review queue.

SQLite is the zero-config warehouse. Set DATABASE_URL to a PostgreSQL URL to
store the catalog and published tables there instead. Staging files for human
review always stay on local disk.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine, insert, select, text, update
from sqlalchemy.pool import NullPool

from dataguardian.pipeline_error import PipelineError
from dataguardian.serialize import to_jsonable
from dataguardian.sqlguard import assert_read_only, quote_ident


class Warehouse:
    def __init__(self, home: Path, database_url: str | None = None, *, honor_env: bool = True):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.staging = self.home / "staging"
        self.uploads = self.home / "uploads"
        self.staging.mkdir(exist_ok=True)
        self.uploads.mkdir(exist_ok=True)
        self.url, self.dialect = _resolve_url(
            database_url, self.home / "warehouse.db", honor_env=honor_env
        )
        kwargs = {"poolclass": NullPool}
        connect_args = {"check_same_thread": False} if self.dialect == "sqlite" else {}
        self.engine = create_engine(self.url, connect_args=connect_args, **kwargs)
        self._lock = threading.Lock()
        self.metadata = MetaData()
        self.datasets = Table(
            "dg_datasets",
            self.metadata,
            Column("id", String, primary_key=True),
            Column("table_name", String, nullable=False, unique=True),
            Column("display_name", String, nullable=False),
            Column("source_kind", String),
            Column("source_location", String),
            Column("row_count", Integer),
            Column("column_count", Integer),
            Column("quality_score", Float),
            Column("run_id", String),
            Column("columns_json", String),
            Column("created_at", String, nullable=False),
        )
        self.runs = Table(
            "dg_runs",
            self.metadata,
            Column("id", String, primary_key=True),
            Column("status", String, nullable=False),
            Column("display_name", String),
            Column("report_json", String, nullable=False),
            Column("created_at", String, nullable=False),
        )
        self.reviews = Table(
            "dg_reviews",
            self.metadata,
            Column("id", String, primary_key=True),
            Column("run_id", String, nullable=False),
            Column("status", String, nullable=False),
            Column("staged_path", String, nullable=False),
            Column("report_json", String, nullable=False),
            Column("dataset_id", String),
            Column("created_at", String, nullable=False),
            Column("resolved_at", String),
        )
        self.metadata.create_all(self.engine)

    def publish(self, frame: pd.DataFrame, meta: dict) -> dict:
        dataset_id = uuid.uuid4().hex[:12]
        table_name = f"dg_d_{dataset_id}"
        quote_ident(table_name)
        row = {
            "id": dataset_id,
            "table_name": table_name,
            "display_name": meta["display_name"],
            "source_kind": meta.get("source_kind"),
            "source_location": meta.get("source_location"),
            "row_count": int(len(frame)),
            "column_count": int(len(frame.columns)),
            "quality_score": meta.get("quality_score"),
            "run_id": meta.get("run_id"),
            "columns_json": json.dumps(meta.get("columns") or []),
            "created_at": _now(),
        }
        with self._lock:
            frame.to_sql(table_name, self.engine, if_exists="fail", index=False)
            with self.engine.begin() as connection:
                connection.execute(insert(self.datasets), row)
        return self.get_dataset(dataset_id)

    def hold_for_review(self, run_id: str, frame: pd.DataFrame, report: dict) -> dict:
        review_id = uuid.uuid4().hex[:12]
        path = self.staging / f"{review_id}.csv"
        frame.to_csv(path, index=False)
        row = {
            "id": review_id,
            "run_id": run_id,
            "status": "pending",
            "staged_path": str(path),
            "report_json": json.dumps(to_jsonable(report)),
            "dataset_id": None,
            "created_at": _now(),
            "resolved_at": None,
        }
        with self._lock:
            with self.engine.begin() as connection:
                connection.execute(insert(self.reviews), row)
        return self.get_review(review_id)

    def update_review_report(self, review_id: str, report: dict) -> None:
        with self._lock:
            with self.engine.begin() as connection:
                connection.execute(
                    update(self.reviews)
                    .where(self.reviews.c.id == review_id)
                    .values(report_json=json.dumps(to_jsonable(report)))
                )

    def resolve_review(self, review_id: str, status: str, dataset_id: str | None) -> dict:
        review = self.get_review(review_id)
        if review is None:
            raise PipelineError("Review not found.")
        if review["status"] != "pending":
            raise PipelineError("This review is already resolved.")
        with self._lock:
            with self.engine.begin() as connection:
                connection.execute(
                    update(self.reviews)
                    .where(self.reviews.c.id == review_id)
                    .values(status=status, dataset_id=dataset_id, resolved_at=_now())
                )
        if status == "rejected":
            path = Path(review["staged_path"])
            if path.is_file():
                path.unlink()
        return self.get_review(review_id)

    def read_staged(self, path: str) -> pd.DataFrame:
        file_path = Path(path)
        if not file_path.is_file():
            raise PipelineError("The staged file for this review is missing.")
        return pd.read_csv(file_path, keep_default_na=False, encoding="utf-8-sig")

    def save_run(self, report: dict) -> None:
        row = {
            "id": report["run_id"],
            "status": report["status"],
            "display_name": report.get("display_name"),
            "report_json": json.dumps(to_jsonable(report)),
            "created_at": report["created_at"],
        }
        with self._lock:
            with self.engine.begin() as connection:
                connection.execute(insert(self.runs), row)

    def list_datasets(self) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.datasets).order_by(self.datasets.c.created_at.desc())).mappings()
            return [_dataset(dict(row)) for row in rows]

    def get_dataset(self, dataset_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.datasets).where(self.datasets.c.id == dataset_id)).mappings().first()
        return None if row is None else _dataset(dict(row))

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.runs).order_by(self.runs.c.created_at.desc()).limit(limit)).mappings()
            reports = []
            for row in rows:
                report = json.loads(row["report_json"])
                reports.append(
                    {
                        "run_id": row["id"],
                        "status": row["status"],
                        "display_name": row["display_name"],
                        "created_at": row["created_at"],
                        "decision": report.get("decision"),
                        "score_after": (report.get("validation") or {}).get("score_after"),
                        "dataset_id": report.get("dataset_id"),
                        "review_id": report.get("review_id"),
                    }
                )
            return reports

    def get_run(self, run_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.runs).where(self.runs.c.id == run_id)).mappings().first()
        if row is None:
            return None
        return json.loads(row["report_json"])

    def list_reviews(self) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.reviews).order_by(self.reviews.c.created_at.desc())).mappings()
            return [_review_summary(dict(row)) for row in rows]

    def get_review(self, review_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.reviews).where(self.reviews.c.id == review_id)).mappings().first()
        if row is None:
            return None
        payload = dict(row)
        payload["report"] = json.loads(payload.pop("report_json"))
        return payload

    def preview(self, dataset_id: str, limit: int = 20) -> dict:
        dataset = self.get_dataset(dataset_id)
        if dataset is None:
            raise PipelineError("Dataset not found.")
        limit = max(1, min(int(limit), 200))
        sql = f"SELECT * FROM {quote_ident(dataset['table_name'])} LIMIT :limit"
        rows = self.execute_select(sql, {"limit": limit})
        return {"dataset": dataset, "rows": rows}

    def execute_select(self, sql: str, params: dict | None = None) -> list[dict]:
        statement = assert_read_only(sql)
        with self.engine.connect() as connection:
            result = connection.execute(text(statement), params or {})
            rows = []
            for record in result.mappings():
                rows.append({key: to_jsonable(value) for key, value in dict(record).items()})
            return rows


def _resolve_url(database_url: str | None, sqlite_path: Path, *, honor_env: bool = True) -> tuple[str, str]:
    import os

    url = (database_url or "").strip()
    if not url and honor_env:
        url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return f"sqlite:///{sqlite_path.resolve().as_posix()}", "sqlite"
    if url.startswith("sqlite:"):
        return url, "sqlite"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgresql"):
        return url, "postgresql"
    raise PipelineError("DATABASE_URL must be a SQLite or PostgreSQL URL.")


def _dataset(row: dict) -> dict:
    columns = json.loads(row.pop("columns_json") or "[]")
    row["columns"] = columns
    row["quality_score"] = None if row.get("quality_score") is None else float(row["quality_score"])
    return row


def _review_summary(row: dict) -> dict:
    report = json.loads(row["report_json"])
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
        "dataset_id": row["dataset_id"],
        "display_name": report.get("display_name"),
        "decision_detail": report.get("decision_detail"),
        "score_after": (report.get("validation") or {}).get("score_after"),
        "residual_issues": (report.get("validation") or {}).get("residual_issues") or [],
        "repairs": [
            repair
            for repair in report.get("repairs") or []
            if repair.get("disposition") in {"review", "suggested"} or not repair.get("applied")
        ],
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
