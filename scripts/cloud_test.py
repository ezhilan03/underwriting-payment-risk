"""Offline checks of warehouse conversion; does not connect to GCP."""
import unittest
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch
from cloud_run import main, prepare_rows


class SchemaTests(unittest.TestCase):
    def test_preserves_money_missingness_and_complex_provenance(self):
        rows, schema = prepare_rows([{"amount_minor": 2**53 + 1, "missing": None,
                                      "ok": True, "source": {"version": 2}}])
        self.assertEqual(rows[0]["amount_minor"], 2**53 + 1)
        self.assertEqual(dict(schema)["amount_minor"], "INTEGER")
        self.assertEqual(dict(schema)["missing"], "STRING")
        self.assertIsNone(rows[0]["missing"])
        self.assertEqual(rows[0]["source"], '{"version": 2}')

    def test_rejects_mixed_money_and_floating_types(self):
        with self.assertRaises(ValueError):
            prepare_rows([{"amount_minor": 1}, {"amount_minor": 1.5}])

    def test_rejects_invalid_numbers_and_columns(self):
        for row in ({"n": float("nan")}, {"n": 2**63}, {"bad-name": 1}):
            with self.assertRaises(ValueError):
                prepare_rows([row])

    def test_delivery_contract_with_mock_clients_and_processes(self):
        """Exercise orchestration locally; this is not a remote service test."""
        bq, storage = MagicMock(), MagicMock()
        load = bq.load_table_from_json.return_value
        load.job_id = "mock-load"
        bq.query.return_value.job_id = "mock-query"
        bq.query.return_value.result.return_value = [SimpleNamespace(n=1)]
        storage.bucket.return_value.blob.return_value.generation = 1
        cloud = ModuleType("google.cloud")
        cloud.bigquery = SimpleNamespace(Client=MagicMock(return_value=bq),
                                         LoadJobConfig=lambda **kw: kw,
                                         QueryJobConfig=lambda **kw: kw,
                                         SchemaField=lambda *args, **kw: args)
        cloud.storage = SimpleNamespace(Client=MagicMock(return_value=storage))
        commands = []

        def process(command, **kwargs):
            commands.append((command, kwargs))
            if "risk_platform" in command:
                output = Path(command[-1])
                self.assertFalse(output.exists())
                (output / "tables").mkdir(parents=True)
                (output / "raw").mkdir()
                (output / "manifest.json").write_text('{}')
                (output / "report.json").write_text('{}')
                (output / "raw" / "records.jsonl").write_text('{"id": 1}\n')
                for name in ("features", "outcomes", "exposures", "scores", "quarantine"):
                    (output / "tables" / f"{name}.jsonl").write_text('{"id": 1}\n')
            else:
                env = kwargs["env"]
                suffix = env["RISK_TABLE_SUFFIX"]
                self.assertEqual(env["RISK_FEATURES_TABLE"], "features" + suffix)
                self.assertEqual(env["RISK_DBT_DATASET"], env["RISK_RAW_DATASET"])
                target = Path(env["DBT_TARGET_PATH"])
                target.mkdir(parents=True)
                (target / "run_results.json").write_text(json.dumps({"results": [{"status": "pass"}]}))
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        env = {"RISK_PROJECT_ID": "example-project", "RISK_REGION": "us-central1",
               "RISK_RAW_BUCKET": "raw", "RISK_ARTIFACT_BUCKET": "artifacts",
               "RISK_BQ_DATASET": "risk"}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"google.cloud": cloud}), \
                patch("cloud_run.subprocess.run", side_effect=process), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            main()
        evidence = json.loads(stdout.getvalue())
        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["dbt"]["status"], "passed")
        self.assertEqual(len(commands), 2)
        self.assertEqual(len(evidence["tables"]), 5)
        blob = storage.bucket.return_value.blob.return_value
        for call in blob.upload_from_filename.call_args_list + blob.upload_from_string.call_args_list:
            self.assertEqual(call.kwargs["if_generation_match"], 0)


if __name__ == "__main__":
    unittest.main()
