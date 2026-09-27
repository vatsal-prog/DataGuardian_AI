"""JSON-safe conversions for reports and previews."""

from __future__ import annotations

import datetime as dt
import json
import math

import numpy as np
import pandas as pd


def to_jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return None if math.isnan(number) else number
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    if value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def frame_records(frame: pd.DataFrame, limit: int = 8) -> list[dict]:
    if frame.empty:
        return []
    preview = frame.head(limit).copy()
    for column in preview.columns:
        preview[column] = preview[column].map(lambda value: "" if _is_na(value) else value)
    payload = json.loads(preview.to_json(orient="records", date_format="iso"))
    return payload


def _is_na(value: object) -> bool:
    if value is pd.NA:
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False
