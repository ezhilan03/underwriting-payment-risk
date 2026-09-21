#!/usr/bin/env python3
"""Load generated JSONL, execute warehouse SQL checks, and optionally run dbt."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {
    'features': 'application_id VARCHAR, consumer_id VARCHAR, decided_at TIMESTAMPTZ, currency VARCHAR, report_id VARCHAR, report_version INTEGER, report_effective_at TIMESTAMPTZ, report_received_at TIMESTAMPTZ, history_missing INTEGER, debt_ratio DOUBLE, history_months INTEGER, prior_returns INTEGER, prior_payments INTEGER, original_approved BOOLEAN, split VARCHAR',
    'outcomes': 'application_id VARCHAR, consumer_id VARCHAR, currency VARCHAR, decided_at TIMESTAMPTZ, mature BOOLEAN, attempted_minor BIGINT, settled_minor BIGINT, returned_minor BIGINT, recovered_minor BIGINT, outstanding_minor BIGINT, return_count INTEGER, payment_count INTEGER, observed BOOLEAN',
    'scores': 'application_id VARCHAR, model_version VARCHAR, feature_version VARCHAR, data_cutoff TIMESTAMPTZ, scored_at TIMESTAMPTZ, score_context VARCHAR, risk_score DOUBLE, split VARCHAR, consumer_seen_in_train BOOLEAN',
}


def render_sql(text: str) -> str:
    text = re.sub(r"\{\{\s*source\('risk_raw',\s*'([^']+)'\)\s*\}\}", r'risk_raw.\1', text)
    return re.sub(r"\{\{\s*ref\('([^']+)'\)\s*\}\}", r'main.\1', text)


def verify(tables: Path, db_path: Path, run_dbt: bool = True) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute('CREATE SCHEMA IF NOT EXISTS risk_raw')
    row_counts = {}
    for name, schema in SCHEMAS.items():
        rows = [json.loads(line) for line in (tables / f'{name}.jsonl').read_text().splitlines() if line.strip()]
        fields = [col.strip().split()[0] for col in schema.split(',')]
        con.execute(f'CREATE OR REPLACE TABLE risk_raw.{name} ({schema})')
        if rows:
            con.executemany(f"INSERT INTO risk_raw.{name} VALUES ({','.join('?' for _ in fields)})", [[row.get(field) for field in fields] for row in rows])
        row_counts[name] = len(rows)
    quarantine = [line for line in (tables / 'quarantine.jsonl').read_text().splitlines() if line.strip()]
    con.execute('CREATE OR REPLACE TABLE risk_raw.quarantine (record JSON)')
    if quarantine:
        con.executemany('INSERT INTO risk_raw.quarantine VALUES (?)', [(line,) for line in quarantine])
    row_counts['quarantine'] = len(quarantine)
    # A previous dbt build materializes views, whereas direct verification uses
    # tables. Remove only this verifier's named outputs before rebuilding.
    for folder in ['marts', 'staging']:
        for path in sorted((ROOT / 'warehouse/models' / folder).glob('*.sql')):
            existing = con.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='main' AND table_name=?", [path.stem]).fetchone()
            if existing:
                kind = 'VIEW' if existing[0] == 'VIEW' else 'TABLE'
                con.execute(f'DROP {kind} main.{path.stem}')
    for folder in ['staging', 'marts']:
        for path in sorted((ROOT / 'warehouse/models' / folder).glob('*.sql')):
            con.execute(f'CREATE OR REPLACE TABLE main.{path.stem} AS ' + render_sql(path.read_text()))
    checks = {}
    for path in sorted((ROOT / 'warehouse/tests').glob('*.sql')):
        checks[path.stem] = len(con.execute(render_sql(path.read_text())).fetchall())
    for table in ['features', 'outcomes']:
        checks[f'{table}_unique_application_id'] = con.execute(f'SELECT count(*) - count(distinct application_id) FROM risk_raw.{table}').fetchone()[0]
    checks['outcomes_orphans'] = con.execute('SELECT count(*) FROM risk_raw.outcomes o LEFT JOIN risk_raw.features f USING (application_id) WHERE f.application_id IS NULL').fetchone()[0]
    checks['scores_orphans'] = con.execute('SELECT count(*) FROM risk_raw.scores s LEFT JOIN risk_raw.features f USING (application_id) WHERE f.application_id IS NULL').fetchone()[0]
    checks['valid_splits'] = con.execute("SELECT count(*) FROM risk_raw.features WHERE split IS NULL OR split NOT IN ('train','validation','test','censored')").fetchone()[0]
    checks['model_versions'] = con.execute("SELECT count(*) FROM risk_raw.scores WHERE model_version IS NULL OR model_version NOT IN ('baseline','challenger')").fetchone()[0]
    checks['nonempty_features'] = int(row_counts['features'] == 0)
    checks['outcome_required_fields'] = con.execute('SELECT count(*) FROM risk_raw.outcomes WHERE consumer_id IS NULL OR currency IS NULL OR decided_at IS NULL OR settled_minor IS NULL OR attempted_minor IS NULL OR returned_minor IS NULL OR recovered_minor IS NULL OR outstanding_minor IS NULL OR return_count IS NULL OR payment_count IS NULL').fetchone()[0]
    cursor = con.execute('SELECT * FROM main.mart_risk_cohorts ORDER BY split, currency, original_approved')
    names = [column[0] for column in cursor.description]
    cohort_rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    con.close()
    # Persist standard JSON; SQL NULL values remain null, including undefined rates.
    cohort_rows = [{k: (None if isinstance(v, float) and v != v else v) for k, v in row.items()} for row in cohort_rows]
    result = {'source_rows': row_counts, 'sql_checks': checks, 'sql_passed': all(value == 0 for value in checks.values()), 'cohorts': cohort_rows, 'dbt_status': 'not_run'}
    if run_dbt:
        env = dict(os.environ, RISK_DUCKDB_PATH=str(db_path.resolve()), DBT_TARGET='local', RISK_RAW_DATASET='risk_raw', RISK_TABLE_SUFFIX='')
        env.update({f'RISK_{name.upper()}_TABLE': name for name in [*SCHEMAS, 'quarantine']})
        command = [str(Path(sys.executable).parent / 'dbt'), 'build', '--project-dir', str(ROOT / 'warehouse'), '--profiles-dir', str(ROOT / 'warehouse')]
        try:
            process = subprocess.run(command, text=True, capture_output=True, env=env)
            log = process.stdout + process.stderr
            result['dbt_status'] = 'passed' if process.returncode == 0 else 'failed'
            result['dbt_returncode'] = process.returncode
        except OSError as exc:
            log = str(exc)
            result['dbt_status'] = 'unavailable'
            result['dbt_returncode'] = None
        (tables.parent / 'dbt-build.log').write_text(log)
    (tables.parent / 'warehouse-verification.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tables', type=Path, default=ROOT / 'artifacts/latest/tables')
    parser.add_argument('--database', type=Path, default=ROOT / 'artifacts/risk.duckdb')
    parser.add_argument('--skip-dbt', action='store_true', help='Run direct SQL checks only; does not claim dbt execution.')
    args = parser.parse_args()
    result = verify(args.tables, args.database, not args.skip_dbt)
    print(json.dumps({key: value for key, value in result.items() if key != 'cohorts'}, indent=2))
    return 0 if result['sql_passed'] and result['dbt_status'] in ('passed', 'not_run') else 1


if __name__ == '__main__':
    raise SystemExit(main())
