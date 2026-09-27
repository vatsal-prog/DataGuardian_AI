"""Dashboard and HTTP API for the pipeline, the review queue, and questions."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dataguardian import __version__
from dataguardian.paths import default_home
from dataguardian.pipeline import Pipeline, sample_spec
from dataguardian.pipeline_error import PipelineError
from dataguardian.sources import DEMO_RECORDS, SourceSpec
from dataguardian.storage import Warehouse

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class RunBody(BaseModel):
    kind: str
    path: str | None = None
    url: str | None = None
    query: str | None = None
    name: str | None = None


class AskBody(BaseModel):
    question: str
    dataset_id: str | None = None


def create_app(home: Path | None = None, database_url: str | None = None, *, honor_env: bool = True) -> FastAPI:
    warehouse = Warehouse(home or default_home(), database_url, honor_env=honor_env)
    pipeline = Pipeline(warehouse)
    app = FastAPI(title="DataGuardian", version=__version__)
    app.state.warehouse = warehouse
    app.state.pipeline = pipeline

    @app.exception_handler(PipelineError)
    async def _pipeline_error(_request: Request, exc: PipelineError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "warehouse": warehouse.dialect,
            "datasets": len(warehouse.list_datasets()),
        }

    @app.get("/api/demo/feed")
    def demo_feed() -> dict:
        return {"data": DEMO_RECORDS}

    @app.get("/api/runs")
    def runs() -> list[dict]:
        return warehouse.list_runs()

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        report = warehouse.get_run(run_id)
        if report is None:
            raise PipelineError("Run not found.")
        return report

    @app.get("/api/datasets")
    def datasets() -> list[dict]:
        return warehouse.list_datasets()

    @app.get("/api/datasets/{dataset_id}")
    def dataset_detail(dataset_id: str, limit: int = 20) -> dict:
        return warehouse.preview(dataset_id, limit)

    @app.get("/api/reviews")
    def reviews() -> list[dict]:
        return warehouse.list_reviews()

    @app.post("/api/pipeline/samples/{name}")
    async def run_sample(name: str) -> dict:
        return await asyncio.to_thread(pipeline.run, sample_spec(name))

    @app.post("/api/pipeline/run")
    async def run_source(body: RunBody) -> dict:
        location = body.url or body.path or ""
        if body.kind in {"postgres", "postgresql", "sqlite"} and body.url and not body.path:
            location = body.url
        spec = SourceSpec(kind=body.kind, location=location, query=body.query, display_name=body.name)
        return await asyncio.to_thread(pipeline.run, spec)

    @app.post("/api/pipeline/upload")
    async def upload(file: UploadFile = File(...)) -> dict:
        name = file.filename or "upload.csv"
        suffix = Path(name).suffix.lower()
        if suffix not in {".csv", ".json"}:
            raise PipelineError("Upload a CSV or JSON file.")
        raw = await file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise PipelineError("File exceeds the 20 MB limit.")
        destination = warehouse.uploads / f"{uuid.uuid4().hex[:12]}{suffix}"
        destination.write_bytes(raw)
        spec = SourceSpec(
            kind="json" if suffix == ".json" else "csv",
            location=str(destination),
            display_name=Path(name).stem,
        )
        return await asyncio.to_thread(pipeline.run, spec)

    @app.post("/api/query")
    async def query(body: AskBody) -> dict:
        return await asyncio.to_thread(pipeline.ask, body.question, body.dataset_id)

    @app.post("/api/reviews/{review_id}/approve")
    async def approve(review_id: str) -> dict:
        return await asyncio.to_thread(pipeline.approve, review_id)

    @app.post("/api/reviews/{review_id}/reject")
    async def reject(review_id: str) -> dict:
        return await asyncio.to_thread(pipeline.reject, review_id)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
