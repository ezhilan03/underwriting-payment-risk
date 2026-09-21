"""Reproducible batch with a completion manifest and byte-level verification."""
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import tempfile
from .data import generate
from .ingestion import Ledger, canonical, digest
from .transforms import normalize, features, outcomes
from .experiment import run_experiment
from .registry import Registry


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical(row)+"\n" for row in rows))


def verify(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"artifact verification failed: {name}")
    return manifest


def dashboard(report):
    body = ""
    for name, result in report["experiment"]["evaluation"].items():
        test = result["test"]
        body += f"<tr><td>{html.escape(name)}</td><td>{test['count']}</td><td>{test['roc_auc']:.3f}</td><td>{test['brier']:.3f}</td><td>{test['cold_start']['count']}</td></tr>"
    policies = ""
    for row in report["experiment"]["policy"]:
        policies += "<tr>" + "".join(f"<td>{html.escape(str(row[key]))}</td>" for key in ("source", "currency", "model", "threshold", "approval_count", "attempted_minor", "fee_revenue_minor", "net_contribution_minor")) + "</tr>"
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Underwriting & Payment Risk — Evidence</title><style>body{{font:16px/1.6 system-ui;margin:0;background:#f3f5f4;color:#172e2b}}main{{max-width:1150px;margin:auto;padding:40px 24px}}h1{{font-size:clamp(2rem,4vw,3.5rem);line-height:1.1;max-width:800px}}h2{{margin-top:40px}}.tag{{color:#286453;font-weight:700}}table{{border-collapse:collapse;width:100%;background:white;font-size:14px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #d8e0de}}.scroll{{overflow:auto}}a{{color:#19694e}}p{{max-width:900px}}</style><main><p class="tag">SYNTHETIC RESEARCH DEMO · LOCAL VERIFICATION</p><h1>Underwriting &amp; Payment Risk</h1><p>Reproducible vendor ingestion, knowledge-time features, temporal model evaluation and separate approval-policy experiments. No real consumer records or live credit decisions.</p><p><strong>{report['counts']['enrollments']:,} applications</strong> · {report['counts']['quarantined']} quarantined records · duplicate replay inserted {report['replay']['inserted']} rows.</p><h2>Held-out observed outcomes</h2><p>Trained on historical approvals only. Consumer-cluster uncertainty intervals and calibration bins are in the <a href="report.json">full report</a>. Selected model: <strong>{html.escape(report['experiment']['selected_model'])}</strong>, chosen using validation Brier score.</p><div class="scroll"><table><tr><th>Model</th><th>Test count</th><th>ROC AUC</th><th>Brier ↓</th><th>Cold-start count</th></tr>{body}</table></div><h2>Policy comparisons</h2><p>Amounts are integer minor units in each currency. Attempted volume is separate from fee revenue. All-consumer outcomes are generated counterfactuals, not observational or causal proof. Net contribution assumes a 2% fee, 25-unit attempt cost, 150-unit return cost, and platform liability for uncollected principal.</p><div class="scroll"><table><tr><th>Evidence</th><th>Currency</th><th>Model</th><th>Threshold</th><th>Approvals</th><th>Attempted</th><th>Fees</th><th>Net contribution</th></tr>{policies}</table></div><h2>Operational boundaries</h2><p>This artifact establishes local execution. GCP deployment, BigQuery runtime, hosted CI, container execution and cloud restoration need separate verification. Promotion and rollback shown in the report are test fixtures, not human-approved production changes.</p><p><a href="manifest.json">Checksum manifest</a> · <a href="report.json">Experiment and verification evidence</a></p></main></html>"""


def run(output, seed=42, applications=3600):
    output = Path(output).resolve()
    config = dict(seed=seed, applications=applications, version="0.1.0", as_of="2025-02-01T00:00:00Z", implementation_hash=digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))}))
    if output.exists():
        manifest = verify(output)
        if manifest["configuration"] != config:
            raise ValueError("immutable output exists with different configuration; choose a new output")
        return json.loads((output/"report.json").read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".risk-build-", dir=output.parent))
    try:
        deliveries, latent = generate(seed, applications)
        ledger = Ledger(temporary/"ledger.sqlite")
        ingestion = ledger.ingest(deliveries)
        raw = ledger.rows()
        raw_hash = digest(raw)
        replay = ledger.ingest(deliveries)
        assert replay["inserted"] == 0 and digest(ledger.rows()) == raw_hash
        entities, relational_quarantine = normalize(raw)
        quarantined = ledger.quarantined() + relational_quarantine
        ledger.close()
        # SQLite is runtime state; portable normalized/raw JSONL is recovery source.
        (temporary/"ledger.sqlite").unlink()
        feature_rows = features(entities)
        outcome_rows = outcomes(entities, config["as_of"])
        training_labels = outcomes(entities, "2024-07-01T00:00:00Z")
        validation_labels = outcomes(entities, "2024-10-01T00:00:00Z")
        experiment, scores = run_experiment(feature_rows, outcome_rows, latent, seed, training_labels, validation_labels, temporary/"models")
        # Original enrollment snapshots are invariant to arrival of late revisions.
        before = dict(entities, reports=[r for r in entities["reports"] if r["version"] == 1])
        assert features(before) == feature_rows
        registry = Registry(temporary/"registry.sqlite")
        evidence_hash = digest(experiment)
        registry.promote("baseline", "threshold-0.5", "automated-test-fixture", "fixture initial version", 0, evidence_hash)
        registry.promote("challenger", "validation-policy", "automated-test-fixture", "fixture reviewed promotion", 1, evidence_hash)
        registry.promote("baseline", "threshold-0.5", "automated-test-fixture", "fixture rollback", 2, evidence_hash, rollback_to=1)
        events = registry.events()
        registry.close()
        (temporary/"registry.sqlite").unlink()
        # Recover from portable source and compare the complete features/outcome tables.
        write_rows(temporary/"raw"/"accepted.jsonl", raw)
        restored = Ledger(temporary/"restore.sqlite")
        recovered_source = [json.loads(line) for line in (temporary/"raw"/"accepted.jsonl").read_text().splitlines()]
        restored.ingest(recovered_source)
        restored_entities, _ = normalize(restored.rows())
        assert features(restored_entities) == feature_rows
        assert outcomes(restored_entities, config["as_of"]) == outcome_rows
        restored.close()
        (temporary/"restore.sqlite").unlink()
        report = dict(configuration=config, counts=dict(enrollments=len(feature_rows), valid_raw=len(raw), deliveries=len(deliveries), quarantined=len(quarantined), payments=len(entities["payments"])), ingestion=ingestion, replay=replay, checks=dict(duplicate_safe_replay=True, late_corrections_preserve_enrollment=True, portable_source_restore=True, model_artifact_restore=True, registry_rollback_fixture=True), experiment=experiment, registry_fixture=events, deployment=dict(local_batch="verified", gcp="not_deployed", hosted_ci="not_run", container="not_run"))
        write_rows(temporary/"raw"/"deliveries.jsonl", deliveries)
        write_rows(temporary/"raw"/"accepted.jsonl", raw)
        write_rows(temporary/"simulation"/"latent.jsonl", latent)
        for name, rows in dict(features=feature_rows, outcomes=outcome_rows, scores=scores, quarantine=quarantined).items():
            write_rows(temporary/"tables"/f"{name}.jsonl", rows)
        write_json(temporary/"report.json", report)
        (temporary/"index.html").write_text(dashboard(report))
        files = {str(path.relative_to(temporary)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(temporary.rglob("*")) if path.is_file()}
        write_json(temporary/"manifest.json", dict(configuration=config, input_hash=digest(deliveries), feature_hash=digest(feature_rows), files=files))
        verify(temporary)
        os.rename(temporary, output)
        return report
    except BaseException:
        shutil.rmtree(temporary)
        raise
