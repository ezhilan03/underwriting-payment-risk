# Learning and interview drills

1. **Late data:** run `test_late_return_changes_outcome_not_features`. The return occurred inside the label window but arrived after maturity. Explain why the latest outcome changes while the enrollment feature and frozen model-training label must not.
2. **Failed payments:** run `test_failed_attempt_preserves_volume_without_revenue`. A 10,000-minor-unit failed attempt creates attempted volume and an attempt cost, but zero settled/collected principal and zero fee revenue.
3. **Corrections and replay:** inspect `test_late_backdated_report_does_not_rewrite_features` and `test_conflicting_version_rolls_back_entire_batch`. Contrast a legitimate new source version with a mutation of an existing version. Reverse delivery order and show the same normalized result.
4. **Reject bias:** compare `observed_approved_only` with `synthetic_all_consumer_simulation` in `report.json`. Why can't the former establish the profitability of approving previously rejected consumers? What additional randomized or otherwise justified causal evidence would be required outside this toy simulation?
5. **Model versus policy:** compare baseline and challenger at threshold 0.5 first, then the selected model at its validation-selected threshold. Lower return counts can simply reflect fewer approvals. Explain all cohort denominators and distinguish transaction volume from fee revenue.
6. **Temporal validation:** move a collection receipt past July 1. Confirm the frozen training outcome excludes it, even though the final February outcome includes it. Explain the embargo and why a random row split can be optimistic for recurring consumers.
7. **Recovery:** restore the immutable accepted JSONL into a fresh ledger. Compare complete feature/outcome hashes, not only row counts. Corrupt one output byte and confirm `python -m risk_platform verify` rejects it.
8. **Cloud costs:** inspect the Terraform plan before deployment. Identify the services that are on demand, storage that persists, the BigQuery query cap and why these do not constitute a hard monthly spending cap.

Expected limitation statement: synthetic data validates mechanics and the evaluation workflow, not creditworthiness, real-world fairness, compliance, causal uplift or commercial outcomes. The generated ground-truth process is part of the simulator and intentionally separate from model features.
