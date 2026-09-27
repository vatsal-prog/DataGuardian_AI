import pandas as pd

from dataguardian.cleaning import parse_number
from dataguardian.pipeline import Pipeline, sample_spec
from dataguardian.pipeline_error import PipelineError
from dataguardian.sqlguard import assert_read_only
from dataguardian.storage import Warehouse


def test_parse_number_and_sql_guard():
    assert parse_number("$1,240.00") == 1240
    assert parse_number("N/A") is None
    assert parse_number("see-note") is None
    assert assert_read_only("SELECT 1;") == "SELECT 1"
    for statement in ("DROP TABLE dg_datasets", "SELECT 1; SELECT 2", "DELETE FROM t"):
        try:
            assert_read_only(statement)
        except PipelineError:
            continue
        raise AssertionError(statement)


def test_orders_pass_and_customers_pass(tmp_path):
    pipeline = Pipeline(Warehouse(tmp_path, honor_env=False))
    orders = pipeline.run(sample_spec("orders"))
    assert orders["status"] == "pass", orders["validation"]
    assert orders["decision"] == "store"
    assert orders["dataset_id"]
    assert orders["rows_after"] == orders["rows_before"] - 1
    issue_types = {issue["issue_type"] for issue in orders["issues"]}
    assert "exact_duplicates" in issue_types
    assert "numeric_format" in issue_types
    assert "inconsistent_categories" in issue_types
    assert "sentinel_values" in issue_types
    assert orders["root_causes"]
    assert any(repair["applied"] and repair["disposition"] == "auto" for repair in orders["repairs"])
    residual_types = {issue["issue_type"] for issue in orders["validation"]["residual_issues"]}
    assert "invalid_datetime" not in residual_types
    assert "exact_duplicates" not in residual_types
    assert orders["validation"]["score_after"] > orders["validation"]["score_before"]

    customers = pipeline.run(sample_spec("customers"))
    assert customers["status"] == "pass", customers["validation"]
    assert customers["rows_after"] < customers["rows_before"]


def test_broken_ledger_rolls_back_until_review(tmp_path):
    pipeline = Pipeline(Warehouse(tmp_path, honor_env=False))
    report = pipeline.run(sample_spec("broken_ledger"))
    assert report["status"] == "fail", report["validation"]
    assert report["decision"] == "rollback"
    assert report["dataset_id"] is None
    assert report["review_id"]
    assert pipeline.warehouse.list_datasets() == []

    approved = pipeline.approve(report["review_id"])
    assert approved["status"] == "pass", approved["validation"]
    assert approved["dataset_id"]
    stored = pipeline.warehouse.list_datasets()
    assert len(stored) == 1
    assert stored[0]["row_count"] == approved["rows_after"]

    again = pipeline.run(sample_spec("broken_ledger"))
    rejected = pipeline.reject(again["review_id"])
    assert rejected["status"] == "rejected"
    assert len(pipeline.warehouse.list_datasets()) == 1


def test_sqlite_source_round_trip(tmp_path):
    source = tmp_path / "source.db"
    frame = pd.DataFrame(
        {
            "sku": ["A-1", "A-1", "B-2"],
            "price": ["$10.00", "$10.00", "12"],
            "region": ["usa", "usa", "UK"],
        }
    )
    from sqlalchemy import create_engine

    frame.to_sql("catalog", create_engine(f"sqlite:///{source.as_posix()}"), index=False)
    pipeline = Pipeline(Warehouse(tmp_path / "warehouse", honor_env=False))
    from dataguardian.sources import SourceSpec

    report = pipeline.run(
        SourceSpec(
            kind="sqlite",
            location=str(source),
            query="SELECT sku, price, region FROM catalog",
            display_name="catalog",
        )
    )
    assert report["status"] == "pass", report["validation"]
    answer = pipeline.ask("how many catalog")
    assert answer["understood"] is True
    assert answer["rows"][0]["count"] == report["rows_after"]
