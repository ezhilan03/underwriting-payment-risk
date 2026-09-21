from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from sandbox_run import require_no_billing, rows_hash


class SandboxGuards(unittest.TestCase):
    def test_only_explicit_unlinked_billing_is_accepted(self):
        valid = dict(projectId="risk-project", billingEnabled=False, billingAccountName="")
        require_no_billing(valid, "risk-project")
        for invalid in ({}, dict(valid, projectId="other"), dict(valid, billingEnabled=True), dict(valid, billingAccountName="billingAccounts/linked")):
            with self.assertRaises(ValueError):
                require_no_billing(invalid, "risk-project")

    def test_full_row_readback_ignores_order_but_not_changes(self):
        schema = [("id", "STRING"), ("amount", "INTEGER"), ("missing", "STRING")]
        expected = [dict(id="a", amount=100), dict(id="b", amount=200)]
        actual = [dict(id="b", amount=200, missing=None), dict(id="a", amount=100, missing=None)]
        self.assertEqual(rows_hash(expected, schema), rows_hash(actual, schema))
        actual[0]["amount"] = 201
        self.assertNotEqual(rows_hash(expected, schema), rows_hash(actual, schema))
