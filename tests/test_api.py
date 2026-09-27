from fastapi.testclient import TestClient

from dataguardian.web.app import create_app


def test_dashboard_flow(tmp_path):
    client = TestClient(create_app(tmp_path, honor_env=False))

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["warehouse"] == "sqlite"

    page = client.get("/")
    assert page.status_code == 200
    assert "DataGuardian" in page.text
    assert "Root-cause agent" in page.text

    orders = client.post("/api/pipeline/samples/orders")
    assert orders.status_code == 200, orders.text
    body = orders.json()
    assert body["status"] == "pass"
    assert body["decision"] == "store"

    average = client.post(
        "/api/query",
        json={"question": "average amount", "dataset_id": body["dataset_id"]},
    )
    assert average.status_code == 200
    assert average.json()["understood"] is True
    assert "AVG" in average.json()["sql"]
    assert "average" in average.json()["answer"].lower()

    grouped = client.post(
        "/api/query",
        json={"question": "total amount by country", "dataset_id": body["dataset_id"]},
    )
    assert grouped.json()["understood"] is True, grouped.json()
    assert "United States" in {row["country"] for row in grouped.json()["rows"]}

    count = client.post("/api/query", json={"question": "how many orders", "dataset_id": body["dataset_id"]})
    assert count.json()["rows"][0]["count"] == body["rows_after"]

    broken = client.post("/api/pipeline/samples/broken_ledger")
    assert broken.json()["status"] == "fail"
    assert broken.json()["decision"] == "rollback"
    review_id = broken.json()["review_id"]
    assert client.get("/api/datasets").json()[0]["id"] == body["dataset_id"]

    approved = client.post(f"/api/reviews/{review_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pass", approved.json().get("validation")

    demo = client.post(
        "/api/pipeline/run",
        json={"kind": "api", "url": "http://127.0.0.1:9/api/demo/feed", "name": "demo_feed"},
    )
    assert demo.status_code == 200, demo.text
    assert demo.json()["status"] == "pass", demo.json()["validation"]

    rejected_run = client.post("/api/pipeline/samples/broken_ledger")
    rejected = client.post(f"/api/reviews/{rejected_run.json()['review_id']}/reject")
    assert rejected.json()["status"] == "rejected"

    blocked = client.post("/api/pipeline/run", json={"kind": "postgres", "url": "postgresql://localhost/db", "query": "DROP TABLE orders"})
    assert blocked.status_code == 400
