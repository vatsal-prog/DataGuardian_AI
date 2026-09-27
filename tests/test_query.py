from dataguardian.agents.query import parse_question

COLUMNS = [
    {"name": "amount", "inferred_type": "float", "role": "money"},
    {"name": "country", "inferred_type": "categorical", "role": "country"},
    {"name": "status", "inferred_type": "categorical", "role": "default"},
    {"name": "order_id", "inferred_type": "integer", "role": "key"},
]


def test_aggregate_by_and_filter():
    plan, error = parse_question("total amount by country where status is shipped", COLUMNS, "orders")
    assert error is None
    assert "SUM" in plan["sql"]
    assert 'GROUP BY "country"' in plan["sql"]
    assert "LOWER" in plan["sql"]
    assert plan["params"]["where_value"] == "shipped"


def test_count_and_unknown_question():
    plan, error = parse_question("how many orders", COLUMNS, "orders")
    assert error is None
    assert plan["sql"].startswith("SELECT COUNT(*)")
    plan, error = parse_question("what is the meaning of this extract", COLUMNS, "orders")
    assert plan is None
    assert error
