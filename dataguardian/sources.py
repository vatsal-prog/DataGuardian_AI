"""Load tabular data from CSV, JSON, HTTP APIs, SQLite, and PostgreSQL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

import pandas as pd
from sqlalchemy import create_engine, text

from dataguardian.pipeline_error import PipelineError
from dataguardian.sqlguard import assert_read_only

LIST_KEYS = ("data", "results", "items", "records", "rows")

DEMO_RECORDS = [
    {"sku": "PEN-1", "name": "Field pen", "price": "$12.00", "region": "usa", "stock": 40},
    {"sku": "PEN-2", "name": "Field pen", "price": "$12.00", "region": "usa", "stock": 40},
    {"sku": "NB-4", "name": "Field notebook", "price": "18.50", "region": "USA", "stock": 25},
    {"sku": "NB-5", "name": "Grid notebook", "price": "$9.00", "region": "United States", "stock": 30},
    {"sku": "LP-1", "name": "Lamp", "price": "42.00", "region": "UK", "stock": 8},
    {"sku": "LP-2", "name": "Lamp shade", "price": "16.00", "region": "Uk", "stock": 14},
    {"sku": "CP-1", "name": "Mug", "price": "11.00", "region": "Canada", "stock": "N/A"},
    {"sku": "CP-2", "name": "Travel mug", "price": "$24.00", "region": "Canada", "stock": 12},
    {"sku": "BG-1", "name": "Canvas bag", "price": "28.00", "region": "United Kingdom", "stock": 9},
    {"sku": "BG-2", "name": "Leather bag", "price": "120.00", "region": "uk", "stock": 4},
]


@dataclass
class SourceSpec:
    kind: str
    location: str = ""
    query: str | None = None
    display_name: str | None = None


def load_source(spec: SourceSpec) -> pd.DataFrame:
    kind = (spec.kind or "").lower().strip()
    if kind == "csv":
        return _read_csv(spec.location)
    if kind == "json":
        return _read_json(spec.location)
    if kind == "api":
        return _read_api(spec.location)
    if kind in {"postgres", "postgresql", "sqlite"}:
        return _read_sql(spec)
    raise PipelineError(f"Unknown source kind {spec.kind!r}. Use csv, json, api, postgres, or sqlite.")


def demo_frame() -> pd.DataFrame:
    return pd.json_normalize(DEMO_RECORDS)


def _read_csv(path: str) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.is_file():
        raise PipelineError(f"CSV file not found: {path}")
    try:
        return pd.read_csv(file_path, encoding="utf-8-sig", keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise PipelineError(f"{file_path.name} is empty.") from exc
    except UnicodeDecodeError as exc:
        raise PipelineError(f"{file_path.name} is not valid UTF-8.") from exc


def _read_json(path: str) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.is_file():
        raise PipelineError(f"JSON file not found: {path}")
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{file_path.name} is not valid JSON.") from exc
    records = unwrap_records(payload)
    if not records:
        raise PipelineError(f"{file_path.name} does not contain any records.")
    return pd.json_normalize(records)


def _read_api(url: str) -> pd.DataFrame:
    if not url:
        raise PipelineError("An API URL is required.")
    if url.rstrip("/").endswith("/api/demo/feed"):
        return demo_frame()
    try:
        with urlopen(url, timeout=20) as response:  # noqa: S310 - user-supplied data API
            raw = response.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - surface a single readable error
        raise PipelineError(f"Could not fetch {url}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipelineError("The API response was not JSON.") from exc
    records = unwrap_records(payload)
    if not records:
        raise PipelineError("The API response did not contain a list of records.")
    return pd.json_normalize(records)


def _read_sql(spec: SourceSpec) -> pd.DataFrame:
    if not spec.query:
        raise PipelineError("A SELECT query is required for database sources.")
    statement = assert_read_only(spec.query)
    url = _sqlalchemy_url(spec)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return pd.read_sql(text(statement), connection)
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PipelineError(f"Could not read the database source: {exc}") from exc
    finally:
        engine.dispose()


def unwrap_records(payload: object) -> list[dict]:
    if isinstance(payload, list):
        if payload and not isinstance(payload[0], dict):
            raise PipelineError("JSON lists must contain objects.")
        return payload
    if isinstance(payload, dict):
        for key in LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                if value and not isinstance(value[0], dict):
                    raise PipelineError(f"JSON key {key!r} must contain objects.")
                return value
    raise PipelineError(
        "Expected a JSON list of objects, or an object with a data, results, items, records, or rows list."
    )


def _sqlalchemy_url(spec: SourceSpec) -> str:
    kind = spec.kind.lower()
    if kind == "sqlite":
        path = Path(spec.location)
        if not path.is_file():
            raise PipelineError(f"SQLite database not found: {spec.location}")
        return f"sqlite:///{path.resolve().as_posix()}"
    url = (spec.location or "").strip()
    if not url:
        raise PipelineError("A PostgreSQL URL is required.")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url
