"""One-shot synthetic batch delivery. No cloud requests until main() executes."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


def scalar_kind(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise ValueError("Integer exceeds BigQuery INT64")
        return "INTEGER"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric value")
        return "FLOAT"
    if isinstance(value, (str, dict, list)):
        return "STRING"
    raise ValueError(f"Unsupported value type: {type(value).__name__}")


def prepare_rows(rows):
    """Infer scalar columns from every row; reject mixed incompatible types.

    Complex values retain their complete JSON as STRING, avoiding a guess about
    nested record schemas. Null-only columns use nullable STRING.
    """
    kinds = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each JSONL record must be an object")
        for key, value in row.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,299}", key):
                raise ValueError(f"Invalid BigQuery field name: {key}")
            kinds.setdefault(key, set())
            kind = scalar_kind(value)
            if kind:
                kinds[key].add(kind)
    schema = []
    for key, types in sorted(kinds.items()):
        if len(types) > 1:
            # Do not coerce money or identifiers through FLOAT and lose precision.
            raise ValueError(f"Inconsistent types for {key}: {sorted(types)}")
        schema.append((key, next(iter(types), "STRING")))
    normalized = [{key: json.dumps(value, sort_keys=True, allow_nan=False)
                   if isinstance(value, (dict, list)) else value
                   for key, value in row.items()} for row in rows]
    return normalized, schema


def main():
    from google.cloud import bigquery, storage

    project = os.environ["RISK_PROJECT_ID"]
    dataset = os.environ["RISK_BQ_DATASET"]
    region = os.environ["RISK_REGION"]
    raw_bucket = os.environ["RISK_RAW_BUCKET"]
    artifacts_bucket = os.environ["RISK_ARTIFACT_BUCKET"]
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project):
        raise ValueError("Invalid project ID")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dataset):
        raise ValueError("Invalid dataset ID")
    run_id = uuid.uuid4().hex
    storage_client = storage.Client(project=project)
    bq = bigquery.Client(project=project, location=region)
    evidence = {"run_id": run_id, "project": project, "objects": [], "tables": []}
    with tempfile.TemporaryDirectory(prefix="risk-") as directory:
        output = Path(directory) / "batch"
        subprocess.run([sys.executable, "-m", "risk_platform", "run", "--output", str(output)], check=True)
        for required in ("manifest.json", "report.json"):
            if not (output / required).is_file():
                raise RuntimeError(f"Missing required batch artifact: {required}")
        for path in sorted(output.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(output)
            bucket_name = raw_bucket if relative.parts[0] == "raw" else artifacts_bucket
            name = f"runs/{run_id}/{relative.as_posix()}"
            blob = storage_client.bucket(bucket_name).blob(name)
            blob.upload_from_filename(str(path), if_generation_match=0, timeout=120)
            evidence["objects"].append({"uri": f"gs://{bucket_name}/{name}",
                                        "generation": str(blob.generation),
                                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        table_paths = sorted((output / "tables").glob("*.jsonl"))
        if not table_paths:
            raise RuntimeError("Batch produced no JSONL tables")
        for path in table_paths:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,200}", path.stem):
                raise ValueError(f"Invalid table filename: {path.name}")
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if not rows:
                evidence["tables"].append({"name": path.stem, "rows": 0, "status": "empty_skipped"})
                continue
            rows, schema = prepare_rows(rows)
            table_id = f"{project}.{dataset}.{path.stem}_{run_id}"
            config = bigquery.LoadJobConfig(
                schema=[bigquery.SchemaField(name, kind, mode="NULLABLE") for name, kind in schema],
                write_disposition="WRITE_EMPTY", create_disposition="CREATE_IF_NEEDED")
            job = bq.load_table_from_json(rows, table_id, job_config=config,
                                         job_id=f"risk_load_{run_id}_{path.stem}", location=region)
            job.result(timeout=300)
            query = bq.query(f"SELECT COUNT(*) AS n FROM `{table_id}`",
                             job_config=bigquery.QueryJobConfig(maximum_bytes_billed=100_000_000),
                             location=region)
            actual = list(query.result(timeout=120))[0].n
            if actual != len(rows):
                raise RuntimeError(f"Row-count mismatch: {table_id}: {actual} != {len(rows)}")
            evidence["tables"].append({"table_id": table_id, "rows": actual,
                                       "load_job_id": job.job_id, "query_job_id": query.job_id})
        # Copy the project because the nonroot image user cannot write /app.
        project_path = Path(directory) / "warehouse"
        shutil.copytree(Path(__file__).resolve().parents[1] / "warehouse", project_path,
                        ignore=shutil.ignore_patterns("target", "logs", ".user.yml"))
        dbt_output = Path(directory) / "dbt-evidence"
        dbt_output.mkdir()
        dbt_env = dict(os.environ, DBT_TARGET="bigquery", GCP_PROJECT_ID=project,
                       GCP_REGION=region, RISK_DBT_DATASET=dataset, RISK_RAW_DATASET=dataset,
                       RISK_TABLE_SUFFIX=f"_{run_id}", RISK_MAXIMUM_BYTES_BILLED="100000000",
                       DBT_TARGET_PATH=str(dbt_output / "target"),
                       DBT_LOG_PATH=str(dbt_output / "logs"), DBT_SEND_ANONYMOUS_USAGE_STATS="false")
        for name in ("features", "outcomes", "exposures", "scores", "quarantine"):
            dbt_env[f"RISK_{name.upper()}_TABLE"] = f"{name}_{run_id}"
        process = subprocess.run([str(Path(sys.executable).parent / "dbt"), "build",
                                  "--project-dir", str(project_path), "--profiles-dir", str(project_path)],
                                 env=dbt_env, text=True, capture_output=True, timeout=900)
        (dbt_output / "build.log").write_text(process.stdout + process.stderr)
        for path in sorted(dbt_output.rglob("*")):
            if path.is_file():
                name = f"runs/{run_id}/dbt/{path.relative_to(dbt_output).as_posix()}"
                blob = storage_client.bucket(artifacts_bucket).blob(name)
                blob.upload_from_filename(str(path), if_generation_match=0, timeout=120)
                evidence["objects"].append({"uri": f"gs://{artifacts_bucket}/{name}",
                                            "generation": str(blob.generation),
                                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if process.returncode:
            raise RuntimeError(f"BigQuery dbt build failed; inspect runs/{run_id}/dbt/build.log")
        results = json.loads((dbt_output / "target" / "run_results.json").read_text())["results"]
        if not results or any(result["status"] not in ("success", "pass") for result in results):
            raise RuntimeError("BigQuery dbt build has missing or nonpassing results")
        evidence["dbt"] = {"status": "passed", "results": len(results),
                           "table_suffix": f"_{run_id}"}
        # A completion marker requires loads, row-count checks and dbt models/tests.
        evidence["status"] = "verified"
        storage_client.bucket(artifacts_bucket).blob(f"runs/{run_id}/cloud-verification.json").upload_from_string(
            json.dumps(evidence, sort_keys=True), content_type="application/json", if_generation_match=0)
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
