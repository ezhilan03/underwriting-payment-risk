"""No-billing BigQuery delivery driven locally; never links billing or deploys compute."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloud_run import prepare_rows
from risk_platform.ingestion import Ledger, canonical, digest
from risk_platform.pipeline import verify
from risk_platform.transforms import normalize, features, outcomes, exposures


def require_no_billing(info, project):
    if info.get("projectId") != project or info.get("billingEnabled") is not False or info.get("billingAccountName"):
        raise ValueError("Sandbox requires this exact project with billing disabled and no linked billing account")


def gcloud(configuration, *args):
    return subprocess.check_output(["gcloud", f"--configuration={configuration}", *args], text=True).strip()


def rows_hash(rows, schema):
    fields = [name for name, _ in schema]
    return digest(sorted(canonical({name: row.get(name) for name in fields}) for row in rows))


def run(project, configuration, output, evidence_path):
    from google.cloud import bigquery
    from google.api_core.exceptions import NotFound
    from google.oauth2.credentials import Credentials

    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise ValueError("Invalid project ID")
    billing = json.loads(gcloud(configuration, "billing", "projects", "describe", project, "--format=json"))
    require_no_billing(billing, project)
    manifest = verify(output)
    output = Path(output)
    # A short-lived access token stays in memory and a child process environment.
    # No token file, key, global ADC change or billing modification is made.
    token = gcloud(configuration, "auth", "print-access-token")
    client = bigquery.Client(project=project, credentials=Credentials(token), location="us-central1")
    dataset_id = f"{project}.risk_sandbox"
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = "us-central1"
    dataset.description = "Synthetic underwriting portfolio; billing must remain unlinked"
    dataset.default_table_expiration_ms = 60 * 86400 * 1000
    client.create_dataset(dataset, exists_ok=True)
    tables = {path.stem: [json.loads(line) for line in path.read_text().splitlines()] for path in sorted((output/"tables").glob("*.jsonl"))}
    accepted = [json.loads(line) for line in (output/"raw/accepted.jsonl").read_text().splitlines()]
    tables["raw_events"] = [dict(source_hash=digest(row), source_kind=row["kind"], source_id=row["source_id"], payload=canonical(row)) for row in accepted]
    # Reuse verified source snapshots rather than consume sandbox's lifetime
    # storage allowance again for an unchanged batch.
    run_id = digest(tables)[:16]
    evidence = dict(project=project, mode="bigquery_sandbox", billing=billing, source_manifest_sha256=hashlib.sha256((output/"manifest.json").read_bytes()).hexdigest(), run_id=run_id, tables=[], cloud_compute_deployed=False, gcs_deployed=False)
    restored_raw = None
    for name, original in tables.items():
        rows, schema = prepare_rows(original)
        table_id = f"{dataset_id}.{name}_{run_id}"
        try:
            table = client.get_table(table_id)
            load_id = None
        except NotFound:
            config = bigquery.LoadJobConfig(schema=[bigquery.SchemaField(key, kind) for key, kind in schema], write_disposition="WRITE_EMPTY")
            job = client.load_table_from_json(rows, table_id, job_config=config, location="us-central1")
            job.result(timeout=180)
            load_id = job.job_id
            table = client.get_table(table_id)
        remote = [dict(row) for row in client.list_rows(table, page_size=10000)]
        expected = rows_hash(rows, schema)
        if len(remote) != len(rows) or rows_hash(remote, schema) != expected:
            raise ValueError(f"BigQuery full-row restoration mismatch: {table_id}")
        evidence["tables"].append(dict(table_id=table_id, rows=len(remote), content_sha256=expected, bytes=table.num_bytes, expires=table.expires.isoformat() if table.expires else None, load_job_id=load_id, existing_reused=load_id is None, full_readback_verified=True))
        if name == "raw_events":
            restored_raw = [json.loads(row["payload"]) for row in remote]
            if any(digest(json.loads(row["payload"])) != row["source_hash"] for row in remote):
                raise ValueError("Raw source hash mismatch")
    with tempfile.TemporaryDirectory(prefix="risk-sandbox-") as directory:
        restored = Ledger(Path(directory)/"restored.sqlite")
        restored.ingest(restored_raw)
        entities, quarantine = normalize(restored.rows())
        restored.close()
        if quarantine or features(entities) != tables["features"] or outcomes(entities, manifest["configuration"]["as_of"]) != tables["outcomes"] or exposures(entities, manifest["configuration"]["as_of"]) != tables["exposures"]:
            raise ValueError("Restored cloud sources do not reproduce the native tables")
        evidence["cloud_source_restoration"] = "features_outcomes_exposures_identical"
        project_path = Path(directory)/"warehouse"
        shutil.copytree(ROOT/"warehouse", project_path, ignore=shutil.ignore_patterns("target", "logs", ".user.yml"))
        # Keep the checked-in Cloud Run ADC profile unchanged. This temporary
        # profile references an environment token, never the token literal.
        profile = project_path/"profiles.yml"
        profile.write_text(profile.read_text().replace("method: oauth", "method: oauth-secrets\n      token: \"{{ env_var('DBT_ENV_SECRET_RISK_TOKEN') }}\""))
        env = dict(os.environ, DBT_TARGET="bigquery", GCP_PROJECT_ID=project, GCP_REGION="us-central1", RISK_DBT_DATASET="risk_sandbox", RISK_RAW_DATASET="risk_sandbox", RISK_TABLE_SUFFIX=f"_{run_id}", DBT_ENV_SECRET_RISK_TOKEN=token, RISK_MAXIMUM_BYTES_BILLED="100000000", DBT_SEND_ANONYMOUS_USAGE_STATS="false")
        for name in tables:
            env[f"RISK_{name.upper()}_TABLE"] = f"{name}_{run_id}"
        result = subprocess.run([str(Path(sys.executable).parent/"dbt"), "build", "--project-dir", str(project_path), "--profiles-dir", str(project_path)], env=env, text=True, capture_output=True, timeout=900)
        evidence_path = Path(evidence_path)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        # Defensively redact the ephemeral token if a dependency echoes it.
        (evidence_path.parent/"sandbox-dbt.log").write_text((result.stdout+result.stderr).replace(token, "[REDACTED]"))
        if result.returncode:
            raise RuntimeError("Sandbox dbt build failed; see sandbox-dbt.log")
        results = json.loads((project_path/"target/run_results.json").read_text())["results"]
        if not results or any(row["status"] not in ("success", "pass") for row in results):
            raise ValueError("Incomplete or nonpassing sandbox dbt results")
        evidence["dbt"] = dict(status="passed", results=len(results), models=sum(r["unique_id"].startswith("model.") for r in results), tests=sum(r["unique_id"].startswith("test.") for r in results))
    query = client.query(f"SELECT * FROM `{dataset_id}.mart_risk_cohorts_{run_id}` ORDER BY split, currency, original_approved", job_config=bigquery.QueryJobConfig(maximum_bytes_billed=100_000_000))
    cloud_cohorts = [dict(row) for row in query.result(timeout=120)]
    local_report = json.loads((output/"warehouse-verification.json").read_text())
    if cloud_cohorts != local_report["cohorts"]:
        raise ValueError("BigQuery and DuckDB cohort marts differ")
    evidence.update(cohort_parity=True, query_job_id=query.job_id, query_bytes_processed=query.total_bytes_processed, status="verified")
    evidence["billing_after"] = json.loads(gcloud(configuration, "billing", "projects", "describe", project, "--format=json"))
    require_no_billing(evidence["billing_after"], project)
    evidence_path.write_text(json.dumps(evidence, indent=2)+"\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--configuration", default="portfolio")
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts/latest")
    parser.add_argument("--evidence", type=Path, default=ROOT/"artifacts/verification/sandbox.json")
    args = parser.parse_args()
    result = run(args.project, args.configuration, args.output, args.evidence)
    print(json.dumps({key: result[key] for key in ("project", "mode", "run_id", "status", "dbt", "cohort_parity")}))
