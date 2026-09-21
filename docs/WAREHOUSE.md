# Warehouse implementation

`warehouse/` is a dbt project with local DuckDB and environment-configured BigQuery targets. Three staging views expose features, outcomes and scores. `mart_risk_cohorts` joins one feature and outcome per application, grouped by split, currency and original approval. Source tables are expected in `risk_raw`; quarantine is retained as a raw JSON record table locally.

Run the synthetic pipeline first, then, from the repository root:

```sh
python scripts/verify_warehouse.py
```

The Python environment needs `duckdb` and `dbt-duckdb`, including the `dbt` executable beside Python. The verifier reads the four `artifacts/latest/tables/*.jsonl` files, replaces their local source tables, executes the actual model and singular-test SQL in DuckDB, checks uniqueness and source relationships, and runs `dbt build`. It writes `artifacts/latest/warehouse-verification.json`, `artifacts/latest/dbt-build.log`, and `artifacts/risk.duckdb`. Each invocation is a full local rebuild, not an incremental ingestion proof. `--tables` and `--database` override input and database paths. `--skip-dbt` explicitly performs only direct SQL checks and reports `dbt_status: not_run`; it is not evidence of a successful dbt run.

The mart exposes application counts, maturity counts, observed-mature counts, and unobserved-mature counts. Monetary totals and return/loss rates include **only mature, observed applications**. Unobserved outcomes carry zero placeholders in source data, but they never enter the rate denominator or become negative labels. Immature rows do not enter outcome metrics. Undefined rates are SQL NULL. Amounts remain currency-specific integer minor units; there is no currency conversion or pooled cross-currency loss metric.

Validation covers unique feature/outcome IDs, orphaned outcomes/scores, missing outcome joins, consistent consumer/currency/decision keys, report effective and received times no later than decision, score bounds and split consistency, nonnegative conserved outcome balances, required fields, accepted split/model values, and mart denominators. These are warehouse integrity checks, not model fairness or policy quality claims.

## BigQuery configuration

Install `dbt-bigquery`, authenticate using Application Default Credentials and provision/load source tables before running:

```sh
export DBT_TARGET=bigquery
export GCP_PROJECT_ID=your-project
export GCP_REGION=us-central1
export RISK_RAW_DATASET=risk_raw
export RISK_DBT_DATASET=risk_raw
export RISK_MAXIMUM_BYTES_BILLED=100000000
dbt build --project-dir warehouse --profiles-dir warehouse
```

The profile uses OAuth/ADC, a single dbt worker, a 300-second job timeout, one retry, and a configurable 100 MB maximum bytes billed per query. Use an existing dedicated dataset for both raw and dbt outputs when the runtime account cannot create datasets. Staging explicitly casts decision and report timestamps to TIMESTAMP, supporting UTC ISO strings from BigQuery JSON loads. Other source types must match the generated contracts; BigQuery native tables must be loaded separately. Per-run source tables are mapped with `RISK_FEATURES_TABLE`, `RISK_OUTCOMES_TABLE`, `RISK_SCORES_TABLE`, and `RISK_QUARANTINE_TABLE` (defaults: logical names). Set `RISK_TABLE_SUFFIX` to the same unique run suffix, including its leading underscore, to isolate every dbt model output; the default empty suffix supports local development. The cloud runner sets these values for its run. The local verifier explicitly clears cloud suffix/source overrides. The warehouse verifier only loads DuckDB. BigQuery compilation/execution and cloud provisioning are separate checks and must not be inferred from a local pass.
