"""Ingestion agent: understand a source and give every column a stable name."""

from __future__ import annotations

import pandas as pd

from dataguardian.cleaning import snake_case
from dataguardian.pipeline_error import PipelineError
from dataguardian.sources import SourceSpec, load_source


class IngestionAgent:
    def run(self, spec: SourceSpec) -> dict:
        frame = load_source(spec)
        if frame is None or frame.empty or len(frame.columns) == 0:
            raise PipelineError("The source produced no rows or columns.")

        frame = frame.copy().reset_index(drop=True)
        used: set[str] = set()
        rename: dict[object, str] = {}
        for original in list(frame.columns):
            base = snake_case(str(original))
            name = base
            suffix = 2
            while name in used:
                name = f"{base}_{suffix}"
                suffix += 1
            used.add(name)
            rename[original] = name
        frame = frame.rename(columns=rename)

        schema = []
        renamed = 0
        for original, name in rename.items():
            if str(original) != name:
                renamed += 1
            schema.append(
                {
                    "name": name,
                    "original_name": str(original),
                    "source_dtype": str(frame[name].dtype),
                    "examples": _examples(frame[name]),
                }
            )

        location = spec.location or spec.kind
        notes = [
            f"Loaded {len(frame)} rows and {len(frame.columns)} columns from {spec.kind}.",
            f"Source: {location}",
        ]
        if renamed:
            notes.append(f"Normalized {renamed} column name{'s' if renamed != 1 else ''} to snake_case.")
        else:
            notes.append("Column names were already safe to store.")

        return {
            "frame": frame,
            "schema": schema,
            "source_kind": spec.kind,
            "source_location": location,
            "row_count": int(len(frame)),
            "column_count": int(len(frame.columns)),
            "notes": notes,
        }


def _examples(series: pd.Series, limit: int = 3) -> list[str]:
    found: list[str] = []
    for value in series.tolist():
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        found.append(str(value)[:80])
        if len(found) == limit:
            break
    return found
