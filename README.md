# DataGuardian

DataGuardian takes a messy extract, decides whether it is safe to keep, and only then lets you ask questions about it.

```
                  ┌──────────────────────┐
                  │      DATA SOURCES    │
                  │ CSV / JSON / APIs /  │
                  │ PostgreSQL           │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │   INGESTION AGENT    │
                  │ Understand source &  │
                  │ schema               │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │    DATA PROFILER     │
                  │ Types / statistics / │
                  │ distributions        │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │   QUALITY AGENT      │
                  │ Detect issues        │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │ ROOT-CAUSE AGENT     │
                  │ Why did it happen?   │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │   REPAIR AGENT       │
                  │ Generate safe fixes  │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │  VALIDATION AGENT    │
                  │ Did the fix work?    │
                  └──────────┬───────────┘
                             ↓
                       PASS / FAIL
                        /        \
                    PASS          FAIL
                     ↓              ↓
               Store Data      Rollback /
               in Database     Human Review
                     ↓
            ┌────────┴──────────┐
            │                   │
            ↓                   ↓
       Query Agent         Dashboard
            ↓
       Natural Language
            ↓
       SQL Generation
            ↓
       PostgreSQL
            ↓
       User's Answer
```

Automatic repairs are the ones that do not invent facts: trimming, sentinel tokens, number and date parsing, category spelling, and exact-row deduplication. Sign fixes, dropping incomplete measures, and collapsing conflicting ids wait for a person. If validation still fails, the warehouse is left unchanged.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m dataguardian
```

Open http://127.0.0.1:8000.

The Pipeline screen can run the bundled samples, upload a CSV or JSON file, fetch a JSON API, or read a `SELECT` from SQLite or PostgreSQL. Pass stores the table. Fail opens Review. Ask turns a question into one read-only SQL statement and runs it.

```bash
python -m dataguardian run --sample orders
python -m dataguardian run path/to/file.csv
python -m dataguardian ask "average amount"
```

SQLite is the warehouse when `DATABASE_URL` is unset. The file lives in `.dataguardian/warehouse.db`.

## Samples

| Sample | What it shows |
| --- | --- |
| `orders` | Currency text, country aliases, whitespace, an exact duplicate, a sentinel age, and a bad date. Safe repairs pass, and the rows are stored. |
| `customers` | JSON records with a replayed row, segment casing, and a placeholder city. Passes. |
| `broken_ledger` | Conflicting invoice ids, negative amounts, and measures that are not numbers. Validation fails. Approving the review repairs publishes what remains. |

The dashboard also has a demo JSON feed at `/api/demo/feed`.

## Questions the query agent accepts

The agent is deterministic. It does not call an external model.

- `how many orders`
- `average amount`
- `total amount by country`
- `count by status`
- `average amount where status is shipped`
- `top 5 by amount`
- `show rows where country is United States`

Name a dataset in the question, or pick one in the dashboard. The generated SQL is a single `SELECT`. Writes, multiple statements, and keywords such as `DROP` or `INTO` are rejected. The same guard is applied to database sources.

## PostgreSQL

```bash
docker compose up -d
export DATABASE_URL=postgresql://dataguardian:dataguardian@localhost:5432/dataguardian
python -m dataguardian
```

`postgres://` and `postgresql://` URLs are rewritten for the psycopg driver. Published tables and the catalog (datasets, runs, reviews) go to that database. Review staging files stay under `.dataguardian/staging/`.

To ingest from Postgres, use the database form in the dashboard or:

```bash
python -m dataguardian run "postgresql://dataguardian:dataguardian@localhost:5432/dataguardian" \
  --kind postgres \
  --query "SELECT * FROM orders" \
  --name orders_export
```

## Validation bar

A run is published only when all of these hold after the automatic repairs:

- at least half of the original rows remain
- no high or critical issues remain
- the quality score is at least 70

The score starts at 100. Critical issues cost 30, high 15, medium 5, and low 1.

## Tests

```bash
pytest
```

## Layout

```
dataguardian/
  agents/        ingestion, profiler, quality, root cause, repair, validation, query
  sources.py     CSV, JSON, API, SQLite, PostgreSQL
  pipeline.py    pass stores, fail rolls back into the review queue
  storage.py     warehouse catalog and published tables
  web/           dashboard
samples/         orders, customers, broken ledger
```
