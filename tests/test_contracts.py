import copy
import json
from pathlib import Path
import tempfile
import unittest
from risk_platform.data import generate
from risk_platform.ingestion import Ledger, validate
from risk_platform.transforms import features, normalize, outcomes, exposures
from risk_platform.registry import Registry
from risk_platform.experiment import economics
from risk_platform.pipeline import verify


def fixture():
    base = dict(schema_version=1, version=1, consumer_id="c1", currency="GBP")
    report = dict(schema_version=1, version=1, consumer_id="c1", kind="report", source_id="r1", vendor_id="vendor_a", effective_at="2024-01-01T00:00:00Z", received_at="2024-01-02T00:00:00Z", payload=dict(debt_ratio=.2, history_months=24))
    enrollment = dict(base, kind="enrollment", source_id="a1", decided_at="2024-01-03T00:00:00Z", received_at="2024-01-03T00:00:00Z", report_id="r1", original_approved=True, model_version="old", policy_version="old")
    payment = dict(base, kind="payment", source_id="p1", application_id="a1", attempted_at="2024-01-04T00:00:00Z", received_at="2024-01-04T00:00:00Z", amount_minor=10000, settlement_status="settled")
    returned = dict(base, kind="return", source_id="ret1", payment_id="p1", occurred_at="2024-01-10T00:00:00Z", received_at="2024-01-11T00:00:00Z", returned_amount_minor=6000)
    collection = dict(base, kind="collection", source_id="col1", payment_id="p1", occurred_at="2024-02-01T00:00:00Z", received_at="2024-02-01T00:00:00Z", recovered_amount_minor=2000)
    return [report, enrollment, payment, returned, collection]


class Contracts(unittest.TestCase):
    def test_duplicate_replay_and_durable_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(Path(d)/"source.sqlite")
            self.assertEqual(ledger.ingest(fixture())["inserted"], 5)
            self.assertEqual(ledger.ingest(fixture()*2)["inserted"], 0)
            ledger.close()
            reopened = Ledger(Path(d)/"source.sqlite")
            self.assertEqual(len(reopened.rows()), 5)
            reopened.close()

    def test_conflicting_version_rolls_back_entire_batch(self):
        ledger = Ledger(":memory:")
        ledger.ingest(fixture())
        new = dict(fixture()[0], source_id="r2")
        corrupt = dict(fixture()[0], payload=dict(debt_ratio=.9, history_months=24))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            ledger.ingest([new, corrupt])
        self.assertEqual(len(ledger.rows()), 5)
        ledger.close()

    def test_negative_and_fractional_money_quarantined(self):
        ledger = Ledger(":memory:")
        for bad in (-2, 1.5, True):
            self.assertEqual(ledger.ingest([dict(fixture()[2], amount_minor=bad)])["inserted"], 0)
        self.assertEqual(len(ledger.quarantined()), 3)
        ledger.close()

    def test_malformed_records_quarantine_without_aborting_valid_delivery(self):
        ledger = Ledger(":memory:")
        bad = [dict(fixture()[0], received_at=42), dict(fixture()[0], payload=dict(debt_ratio=float("nan"), history_months=12)), None]
        result = ledger.ingest(bad + fixture())
        self.assertEqual(result["inserted"], 5)
        self.assertEqual(result["invalid_deliveries"], 3)
        self.assertEqual(len(ledger.quarantined()), 3)
        for row in ledger.quarantined():
            json.loads(row["payload"])
        ledger.close()

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            validate(dict(fixture()[0], received_at="2024-01-02T00:00:00"))

    def test_unsupported_schema_rejected(self):
        with self.assertRaises(ValueError):
            validate(dict(fixture()[0], schema_version=2))

    def test_late_backdated_report_does_not_rewrite_features(self):
        raw = fixture()
        original = features(normalize(raw)[0])
        raw.append(dict(raw[0], version=2, received_at="2024-02-01T00:00:00Z", payload=dict(debt_ratio=.99, history_months=24)))
        self.assertEqual(features(normalize(raw)[0]), original)

    def test_future_effective_report_not_eligible(self):
        raw = fixture()
        raw[0]["effective_at"] = "2024-01-04T00:00:00Z"
        raw[0]["received_at"] = "2024-01-04T00:00:00Z"
        row = features(normalize(raw)[0])[0]
        self.assertIsNone(row["debt_ratio"])
        self.assertEqual(row["history_missing"], 1)

    def test_missing_history_not_zero(self):
        raw = fixture()
        raw[0]["payload"] = dict(debt_ratio=None, history_months=None)
        row = features(normalize(raw)[0])[0]
        self.assertIsNone(row["history_months"])
        self.assertIsNone(row["debt_ratio"])

    def test_second_vendor_normalizes_identically(self):
        raw = fixture()
        expected = features(normalize(raw)[0])
        raw[0].update(vendor_id="vendor_b", payload=dict(obligation_pct=20, history_years=2))
        self.assertEqual(features(normalize(raw)[0]), expected)

    def test_future_return_excluded_from_enrollment_features(self):
        entities, _ = normalize(fixture())
        self.assertEqual(features(entities)[0]["prior_returns"], 0)

    def test_late_return_changes_outcome_not_features(self):
        raw = fixture()[:4]
        raw[3]["received_at"] = "2024-04-01T00:00:00Z"
        entities, _ = normalize(raw)
        before = outcomes(entities, "2024-03-15T00:00:00Z")[0]
        after = outcomes(entities, "2024-04-02T00:00:00Z")[0]
        self.assertTrue(before["mature"])
        self.assertEqual(before["returned_minor"], 0)
        self.assertEqual(after["returned_minor"], 6000)
        self.assertEqual(features(entities), features(normalize(raw[:3])[0]))

    def test_immature_outcomes_flagged(self):
        row = outcomes(normalize(fixture())[0], "2024-02-02T00:00:00Z")[0]
        self.assertFalse(row["mature"])

    def test_recovery_balance(self):
        row = outcomes(normalize(fixture())[0], "2024-04-01T00:00:00Z")[0]
        self.assertEqual((row["attempted_minor"], row["returned_minor"], row["recovered_minor"], row["outstanding_minor"]), (10000, 6000, 2000, 4000))

    def test_exposure_snapshot_tracks_balance_and_age(self):
        entities, _ = normalize(fixture())
        self.assertEqual(exposures(entities, "2024-01-03T00:00:00Z"), [])
        early = exposures(entities, "2024-01-12T00:00:00Z")[0]
        self.assertEqual(early["outstanding_minor"], 6000)
        self.assertEqual(early["age_days"], 8)
        later = exposures(entities, "2024-02-02T00:00:00Z")[0]
        self.assertEqual(later["outstanding_minor"], 4000)
        self.assertNotEqual(early["snapshot_id"], later["snapshot_id"])

    def test_exposure_keeps_returns_outside_model_window(self):
        raw = fixture()[:4]
        raw[3].update(occurred_at="2024-02-20T00:00:00Z", received_at="2024-02-21T00:00:00Z")
        entities, _ = normalize(raw)
        self.assertEqual(exposures(entities, "2024-03-01T00:00:00Z")[0]["outstanding_minor"], 6000)
        self.assertEqual(outcomes(entities, "2024-03-01T00:00:00Z")[0]["outstanding_minor"], 0)

    def test_over_recovery_quarantined(self):
        raw = fixture()
        raw[4]["recovered_amount_minor"] = 7000
        entities, quarantine = normalize(raw)
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(entities["collections"], [])

    def test_currency_mismatch_quarantined(self):
        raw = fixture()
        raw[3]["currency"] = "USD"
        entities, quarantine = normalize(raw)
        self.assertEqual(len(quarantine), 2)  # return mismatch and now-unbacked recovery
        self.assertEqual(entities["returns"], [])

    def test_rejected_consumers_have_no_observed_payment_label(self):
        raw = fixture()
        raw[1]["original_approved"] = False
        entities, _ = normalize(raw)
        row = outcomes(entities, "2024-04-01T00:00:00Z")[0]
        self.assertFalse(row["observed"])
        self.assertEqual(row["payment_count"], 0)

    def test_generator_seed_and_latent_separation(self):
        a, latent = generate(7, 100)
        self.assertEqual((a, latent), generate(7, 100))
        self.assertFalse(any("simulated" in row for row in a))
        rejected = {r["source_id"] for r in a if r["kind"] == "enrollment" and not r["original_approved"]}
        self.assertTrue(any(r["application_id"] in rejected for r in latent))
        self.assertFalse(any(r["application_id"] in rejected for r in a if r["kind"] == "payment"))

    def test_profit_is_not_volume(self):
        row = outcomes(normalize(fixture())[0], "2024-04-01T00:00:00Z")[0]
        result = economics([row], {"a1": .1}, .5)
        self.assertEqual(result["collected_minor"], 6000)
        self.assertEqual(result["fee_revenue_minor"], 120)
        self.assertEqual(result["net_contribution_minor"], 120-25-150-4000)

    def test_failed_attempt_preserves_volume_without_revenue(self):
        raw = fixture()[:3]
        raw[2]["settlement_status"] = "failed"
        row = outcomes(normalize(raw)[0], "2024-04-01T00:00:00Z")[0]
        result = economics([row], {"a1": .1}, .5)
        self.assertEqual(result["attempted_minor"], 10000)
        self.assertEqual(result["settled_minor"], 0)
        self.assertEqual(result["collected_minor"], 0)
        self.assertEqual(result["fee_revenue_minor"], 0)
        self.assertEqual(result["net_contribution_minor"], -25)

    def test_training_snapshot_excludes_future_collection(self):
        raw = fixture()
        raw[4]["received_at"] = "2024-08-01T00:00:00Z"
        entities, _ = normalize(raw)
        self.assertEqual(outcomes(entities, "2024-07-01T00:00:00Z")[0]["recovered_minor"], 0)
        self.assertEqual(outcomes(entities, "2025-02-01T00:00:00Z")[0]["recovered_minor"], 2000)

    def test_normalization_is_delivery_order_independent(self):
        raw = fixture()
        self.assertEqual(normalize(raw), normalize(list(reversed(raw))))

    def test_zero_approvals_have_undefined_return_rate(self):
        row = outcomes(normalize(fixture())[0], "2024-04-01T00:00:00Z")[0]
        result = economics([row], {"a1": .9}, .1)
        self.assertIsNone(result["return_rate"])
        self.assertEqual(result["approval_count"], 0)

    def test_registry_requires_review_stale_version_and_exact_rollback(self):
        registry = Registry(":memory:")
        with self.assertRaises(ValueError):
            registry.promote("m1", "p1", "", "reason", 0, "hash")
        registry.promote("m1", "p1", "reviewer", "reason", 0, "hash")
        with self.assertRaisesRegex(ValueError, "stale"):
            registry.promote("m2", "p2", "reviewer", "reason", 0, "hash")
        registry.promote("m2", "p2", "reviewer", "reason", 1, "hash")
        with self.assertRaisesRegex(ValueError, "exact"):
            registry.promote("m1", "p2", "reviewer", "reason", 2, "hash", rollback_to=1)
        restored = registry.promote("m1", "p1", "reviewer", "rollback", 2, "hash", rollback_to=1)
        self.assertEqual(restored["rollback_to"], 1)
        self.assertEqual(len(registry.events()), 3)
        registry.close()


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from risk_platform.pipeline import run
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name)/"run"
        cls.report = run(cls.output, applications=1200)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_replay_returns_identical_verified_result(self):
        from risk_platform.pipeline import run
        self.assertEqual(run(self.output, applications=1200), self.report)
        self.assertEqual(self.report["replay"]["inserted"], 0)
        self.assertTrue(all(self.report["checks"].values()))

    def test_different_config_cannot_reuse_output(self):
        from risk_platform.pipeline import run
        with self.assertRaisesRegex(ValueError, "different configuration"):
            run(self.output, applications=1200, seed=99)

    def test_score_provenance_and_observed_training(self):
        rows = [json.loads(line) for line in (self.output/"tables/scores.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 2400)
        self.assertTrue(all(r["feature_version"] == "pit-v1" and r["data_cutoff"] <= r["scored_at"] for r in rows))
        report = self.report["experiment"]
        expected = min(report["evaluation"], key=lambda k: (report["evaluation"][k]["validation"]["brier"], k))
        self.assertEqual(report["selected_model"], expected)
        self.assertTrue(all(r["currency"] in ("USD", "GBP") for r in report["policy"]))

    def test_tampering_rejected(self):
        import shutil
        altered = Path(self.temp.name)/"altered"
        shutil.copytree(self.output, altered)
        with (altered/"tables/features.jsonl").open("a") as file:
            file.write("{}\n")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            verify(altered)


if __name__ == "__main__":
    unittest.main()
