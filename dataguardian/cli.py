"""Command line entry points for a pipeline run, a question, or the dashboard."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dataguardian.paths import default_home
from dataguardian.pipeline import Pipeline, sample_spec
from dataguardian.sources import SourceSpec
from dataguardian.storage import Warehouse


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="dataguardian", description="DataGuardian data-quality pipeline")
    parser.add_argument("--home", type=Path, default=None, help="Warehouse directory (default: .dataguardian)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Ingest a file or a built-in sample")
    run.add_argument("path", nargs="?", help="CSV, JSON, or SQLite path")
    run.add_argument("--sample", choices=("orders", "customers", "broken_ledger"))
    run.add_argument("--kind", choices=("csv", "json", "api", "postgres", "sqlite"))
    run.add_argument("--query", help="SELECT statement for a database source")
    run.add_argument("--name", help="Dataset name")

    ask = sub.add_parser("ask", help="Ask a question of the warehouse")
    ask.add_argument("question")
    ask.add_argument("--dataset")

    serve = sub.add_parser("serve", help="Start the dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    home = args.home or default_home()
    if args.command == "serve":
        _serve(home, args.host, args.port)
        return
    pipeline = Pipeline(Warehouse(home))
    if args.command == "run":
        report = pipeline.run(_run_spec(args))
        print(_summary(report))
        return
    if args.command == "ask":
        answer = pipeline.ask(args.question, args.dataset)
        print(answer.get("answer"))
        if answer.get("sql"):
            print(answer["sql"])
        return


def _run_spec(args: argparse.Namespace) -> SourceSpec:
    if args.sample:
        return sample_spec(args.sample)
    if not args.path:
        raise SystemExit("Provide a path or --sample.")
    kind = args.kind
    if kind is None:
        suffix = Path(args.path).suffix.lower()
        kind = {".csv": "csv", ".json": "json", ".sqlite": "sqlite", ".db": "sqlite"}.get(suffix)
    if kind is None:
        raise SystemExit("Pass --kind for this source.")
    return SourceSpec(kind=kind, location=args.path, query=args.query, display_name=args.name)


def _summary(report: dict) -> str:
    validation = report["validation"]
    lines = [
        f"{report['display_name']}: {report['status'].upper()} ({report['decision']})",
        f"rows {report['rows_before']} -> {report['rows_after']}",
        f"score {validation['score_before']} -> {validation['score_after']}",
        report["decision_detail"],
        f"issues: {len(report['issues'])}    repairs applied: "
        f"{sum(1 for repair in report['repairs'] if repair.get('applied'))}",
    ]
    return "\n".join(lines)


def _serve(home: Path, host: str, port: int) -> None:
    import uvicorn

    from dataguardian.web.app import create_app

    uvicorn.run(create_app(home), host=host, port=port)


if __name__ == "__main__":
    main()
