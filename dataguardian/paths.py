"""Filesystem locations for samples and the local warehouse."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES_DIR = REPO_ROOT / "samples"


def default_home() -> Path:
    raw = os.environ.get("DG_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (REPO_ROOT / ".dataguardian").resolve()
