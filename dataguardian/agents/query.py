"""Query agent: natural language to a single read-only SQL statement."""

from __future__ import annotations

import re

from dataguardian.cleaning import parse_number
from dataguardian.sqlguard import quote_ident
from dataguardian.storage import Warehouse

_STOP = {
    "how",
    "many",
    "number",
    "of",
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "what",
    "which",
    "show",
    "me",
    "list",
    "all",
    "rows",
    "records",
    "data",
    "please",
    "in",
    "for",
    "from",
    "with",
    "there",
    "do",
    "we",
    "have",
    "get",
    "give",
    "find",
    "tell",
    "and",
    "to",
    "on",
    "at",
    "be",
    "does",
    "did",
    "can",
    "you",
    "i",
    "our",
    "their",
    "that",
    "this",
    "those",
    "these",
    "any",
    "some",
    "value",
    "values",
}
_AGGS = [
    (r"\b(average|avg|mean)\b", "avg"),
    (r"\b(how many|number of|count)\b", "count"),
    (r"\b(sum|total)\b", "sum"),
    (r"\b(minimum|lowest|min)\b", "min"),
    (r"\b(maximum|highest|max)\b", "max"),
]
_FUNCTIONS = {"avg": "AVG", "sum": "SUM", "min": "MIN", "max": "MAX"}
_ALIASES = {"avg": "average", "sum": "sum", "min": "minimum", "max": "maximum"}


class QueryAgent:
    def __init__(self, warehouse: Warehouse):
        self.warehouse = warehouse

    def ask(self, question: str, dataset_id: str | None = None) -> dict:
        question = (question or "").strip()
        if not question:
            return _miss("Ask a question about a published dataset.")
        datasets = self.warehouse.list_datasets()
        if not datasets:
            return _miss("No published datasets yet. A run has to pass validation before it can be queried.")
        dataset = _select_dataset(question, datasets, dataset_id, self.warehouse)
        if dataset is None:
            return _miss("That dataset is not in the warehouse.")
        columns = dataset.get("columns") or []
        plan, error = parse_question(question, columns, dataset["display_name"])
        if error or plan is None:
            names = ", ".join(column["name"] for column in columns)
            return _miss(error or "I could not turn that into SQL.", dataset=dataset, hint=names)
        sql = plan["sql"].replace("{table}", quote_ident(dataset["table_name"]))
        rows = self.warehouse.execute_select(sql, plan["params"])
        answer = _summarize(dataset["display_name"], plan, rows)
        return {
            "understood": True,
            "dataset_id": dataset["id"],
            "dataset_name": dataset["display_name"],
            "sql": sql,
            "rationale": plan["rationale"],
            "columns": list(rows[0].keys()) if rows else plan["columns"],
            "rows": rows,
            "answer": answer,
        }


def parse_question(question: str, columns: list[dict], dataset_name: str) -> tuple[dict | None, str | None]:
    text = " ".join(question.lower().strip().rstrip("?.").replace("_", " ").split())
    dataset_phrase = (dataset_name or "").lower().replace("_", " ").strip()
    if dataset_phrase:
        text = text.replace(dataset_phrase, " ")

    where_phrase = None
    where_match = re.search(r"\bwhere\s+(.+?)\s+(?:=|is|equals)\s+(.+)$", text)
    if where_match:
        where_phrase = (where_match.group(1).strip(), where_match.group(2).strip().strip("\"'"))
        text = text[: where_match.start()]

    group_phrase = None
    group_match = re.search(r"\b(?:by|per)\s+([a-z0-9 ]+)$", text.strip())
    if group_match:
        group_phrase = group_match.group(1).strip()
        text = text[: group_match.start()]

    limit = None
    top_match = re.search(r"\btop\s+(\d+)\b", text)
    if top_match:
        limit = int(top_match.group(1))
        text = text.replace(top_match.group(0), " ")

    show = bool(re.search(r"\b(show|list)\b", question.lower()))
    agg = None
    for pattern, name in _AGGS:
        if re.search(pattern, text):
            agg = name
            text = re.sub(pattern, " ", text, count=1)
            break

    tokens = [token for token in re.sub(r"[^a-z0-9 ]", " ", text).split() if token not in _STOP]
    phrase = " ".join(tokens)
    measure = _match_column(phrase, columns) if phrase else None
    group = _match_column(group_phrase, columns) if group_phrase else None
    where_column = _match_column(where_phrase[0], columns) if where_phrase else None

    if where_phrase and where_column is None:
        return None, f"I couldn't find a column matching '{where_phrase[0]}'."
    if group_phrase and group is None:
        return None, f"I couldn't find a column matching '{group_phrase}'."

    if agg is None and limit and (measure or group):
        agg = "top"
    if agg is None and show:
        agg = "show"
    if agg is None and group is not None:
        agg = "count"
    if agg is None:
        names = ", ".join(column["name"] for column in columns) or "none"
        return None, (
            "Try 'how many rows', 'average amount', 'total amount by country', "
            f"or 'count by status'. Columns: {names}."
        )

    if agg in _FUNCTIONS and measure is None:
        numeric = [
            column
            for column in columns
            if column.get("inferred_type") in {"integer", "float"} and column.get("role") != "key"
        ]
        if len(numeric) == 1:
            measure = numeric[0]
        else:
            names = ", ".join(column["name"] for column in columns)
            return None, f"Which column should I aggregate? Columns: {names}."
    if agg == "top" and measure is None and group is None:
        return None, "Say which column to rank, for example 'top 5 by amount'."

    return _build_plan(agg, measure, group, where_column, None if where_phrase is None else where_phrase[1], limit), None


def _build_plan(agg, measure, group, where_column, where_value, limit) -> dict:
    params: dict = {}
    where_sql = ""
    if where_column is not None and where_value is not None:
        column_sql = quote_ident(where_column["name"])
        number = parse_number(where_value)
        if where_column.get("inferred_type") in {"integer", "float"} and number is not None:
            where_sql = f" WHERE {column_sql} = :where_value"
            params["where_value"] = number
        else:
            where_sql = f" WHERE LOWER(CAST({column_sql} AS TEXT)) = LOWER(:where_value)"
            params["where_value"] = where_value

    measure_name = None if measure is None else measure["name"]
    group_name = None if group is None else group["name"]
    rationale_bits = []

    if agg == "count" and group is None:
        sql = f"SELECT COUNT(*) AS count FROM {{table}}{where_sql}"
        output_columns = ["count"]
        rationale_bits.append("count the rows")
    elif agg == "count" and group is not None:
        grouped = quote_ident(group_name)
        sql = (
            f"SELECT {grouped}, COUNT(*) AS count FROM {{table}}{where_sql} "
            f"GROUP BY {grouped} ORDER BY count DESC LIMIT 100"
        )
        output_columns = [group_name, "count"]
        rationale_bits.append(f"count rows by {group_name}")
    elif agg in _FUNCTIONS and group is None:
        alias = f"{_ALIASES[agg]}_{measure_name}"
        sql = f"SELECT {_FUNCTIONS[agg]}({quote_ident(measure_name)}) AS {quote_ident(alias)} FROM {{table}}{where_sql}"
        output_columns = [alias]
        rationale_bits.append(f"{_ALIASES[agg]} of {measure_name}")
    elif agg in _FUNCTIONS and group is not None:
        alias = f"{_ALIASES[agg]}_{measure_name}"
        grouped = quote_ident(group_name)
        sql = (
            f"SELECT {grouped}, {_FUNCTIONS[agg]}({quote_ident(measure_name)}) AS {quote_ident(alias)} "
            f"FROM {{table}}{where_sql} GROUP BY {grouped} ORDER BY {quote_ident(alias)} DESC LIMIT 100"
        )
        output_columns = [group_name, alias]
        rationale_bits.append(f"{_ALIASES[agg]} of {measure_name} by {group_name}")
    else:
        limit_n = limit or (5 if agg == "top" else 50)
        order = f" ORDER BY {quote_ident(measure_name)} DESC" if measure_name and agg == "top" else ""
        sql = f"SELECT * FROM {{table}}{where_sql}{order} LIMIT {int(limit_n)}"
        output_columns = ["*"]
        rationale_bits.append(f"show {limit_n} rows" + (f" ranked by {measure_name}" if measure_name else ""))

    if where_column is not None:
        rationale_bits.append(f"where {where_column['name']} is {where_value}")

    return {
        "agg": agg,
        "measure": measure_name,
        "group": group_name,
        "sql": sql,
        "params": params,
        "columns": output_columns,
        "rationale": "Interpreted this as: " + ", ".join(rationale_bits) + ".",
        "limit": limit,
    }


def _match_column(phrase: str | None, columns: list[dict]) -> dict | None:
    if not phrase:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    if not normalized:
        return None
    best = None
    best_score = 0
    for column in columns:
        name = column["name"]
        score = 0
        if name == normalized:
            score = 100
        elif normalized in name or name in normalized:
            score = 80
        else:
            overlap = set(normalized.split("_")) & set(name.split("_"))
            overlap.discard("")
            if overlap:
                score = 40 + 15 * len(overlap)
        if score > best_score:
            best = column
            best_score = score
    if best_score < 50:
        return None
    return best


def _select_dataset(question: str, datasets: list[dict], dataset_id: str | None, warehouse: Warehouse) -> dict | None:
    if dataset_id:
        return warehouse.get_dataset(dataset_id)
    lowered = question.lower().replace("_", " ")
    matches = []
    for dataset in datasets:
        name = dataset["display_name"].lower().replace("_", " ")
        if name and name in lowered:
            matches.append(dataset)
    if matches:
        matches.sort(key=lambda dataset: len(dataset["display_name"]), reverse=True)
        return matches[0]
    return datasets[0]


def _summarize(dataset_name: str, plan: dict, rows: list[dict]) -> str:
    label = dataset_name.replace("_", " ")
    if not rows:
        return f"The query returned no rows from {label}."
    agg = plan["agg"]
    measure = plan.get("measure")
    group = plan.get("group")
    if agg == "count" and not group:
        return f"There are {_fmt(rows[0].get('count'))} rows in {label}."
    if agg in _FUNCTIONS and not group:
        key = next(iter(rows[0]))
        pretty = (measure or key).replace("_", " ")
        return f"The {_ALIASES.get(agg, agg)} {pretty} in {label} is {_fmt(rows[0][key])}."
    if group and rows:
        metric_key = next(key for key in rows[0] if key != group)
        leader = rows[0].get(group)
        return (
            f"{len(rows)} {group.replace('_', ' ')} groups in {label}. "
            f"{leader} is the largest ({_fmt(rows[0][metric_key])})."
        )
    return f"Returned {len(rows)} rows from {label}."


def _fmt(value: object) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _miss(message: str, dataset: dict | None = None, hint: str | None = None) -> dict:
    if hint:
        message = f"{message} Columns: {hint}."
    return {
        "understood": False,
        "dataset_id": None if dataset is None else dataset.get("id"),
        "dataset_name": None if dataset is None else dataset.get("display_name"),
        "sql": None,
        "rationale": None,
        "columns": [],
        "rows": [],
        "answer": message,
    }
