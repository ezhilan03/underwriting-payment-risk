# Zero-dollar GCP deployment

Project: **underwriting-risk-ez-2026**. Dataset: `risk_sandbox`, region `us-central1`.

[Open BigQuery](https://console.cloud.google.com/bigquery?project=underwriting-risk-ez-2026)

Billing is disabled and no billing account is linked. This is the spending boundary; it does not depend on delayed budget alerts or staying within a billing-enabled free tier. Do not link billing while the $0 requirement applies.

## What runs where

Local Python generates the synthetic data, trains/evaluates the models and drives upload/dbt. BigQuery stores six source tables, executes four staging views and a cohort mart, and runs seventeen dbt tests. The pipeline read back every loaded field and restored the raw events from BigQuery into a fresh local ledger. Recomputed features, outcomes and exposures match local artifacts. BigQuery and DuckDB cohort marts also match exactly.

Cloud Run, GCS and Artifact Registry remain optional, undeployed components. Their Terraform configuration has a default-deny opt-in guard. There is no scheduler or always-on compute.

## Run or recover

With the project environment installed, `gcloud` authenticated and the saved local artifact manifest verified:

```sh
python scripts/sandbox_run.py --project underwriting-risk-ez-2026
```

The script:

1. Refuses to proceed unless this project has billing explicitly disabled and no billing-account link.
2. Verifies local artifact checksums and uses an in-memory, short-lived gcloud token. It does not create keys, write token files or alter global ADC.
3. Reuses content-addressed source tables for an unchanged batch. A differing row/hash causes failure rather than overwrite.
4. Runs real BigQuery dbt builds with isolated model names and a 100 MB per-query cap. This uses batch loads and CREATE statements, not unsupported streaming or DML.
5. Verifies full source readback, cloud-source restoration, model/test results, exact cohort parity and billing state again. Evidence goes to `artifacts/verification/sandbox.json`; the local dbt log is git-ignored and token-redacted.

Tables/views expire after 60 days (initial source expiry: November 20, 2026). Rerun from the GitHub/local artifacts to recreate expired data. The sandbox storage allowance is a lifetime quota, so avoid unnecessary new snapshots; deletion does not refund that quota. Model artifact restoration remains local; this path does not claim a remote model-serving service.

## Limits verified against Google documentation

BigQuery Sandbox permits use without a credit card or billing account, with 10 GiB lifetime storage and 1 TiB of processed query data per month. It does not support streaming, DML or the Data Transfer Service. Our six source tables occupy roughly 8.2 MB of logical storage, well within that allowance.

Sources: [BigQuery Sandbox and limitations](https://docs.cloud.google.com/bigquery/docs/sandbox), [Google Cloud Free Tier billing requirement](https://docs.cloud.google.com/free/docs/free-cloud-features).
