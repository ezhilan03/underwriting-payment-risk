# Underwriting / Payment Risk — $0 sandbox deployment verified

21 September 2026. Repository: https://github.com/ezhilan03/underwriting-payment-risk

## Implemented and locally verified

- 3,600 seeded synthetic applications, 4,954 payment attempts/exposure snapshots, 7,200 retrospective model scores and one deliberately invalid quarantined delivery.
- Two vendor schemas; original source versions, enrollment scores/decisions and synthetic residence/check provenance preserved. No residence or check-status fields enter the model feature allowlist.
- Duplicate-safe transactional ingestion, point-in-time features, frozen temporal training/validation labels, explicit maturity, separate currencies and attempted/settled/collected amounts.
- Logistic baseline and gradient-boosted challenger, calibration/ranking metrics, consumer-cluster Brier intervals, cold-start reporting and separate observed-versus-simulated policy economics.
- Fitted model artifact restoration reproduces every score. Portable accepted-source restoration reproduces features, outcomes and exposures. Registry promotion/rollback is explicitly an automated demonstration fixture.
- 32 application/guard tests and four offline cloud-delivery tests pass. Five dbt models and seventeen data tests pass; fifteen direct SQL checks pass. Native local warehouse can be rebuilt after dbt without a table/view conflict.
- Terraform provider initialization, formatting and validation pass. Locked dependencies and a digest-pinned Python base image are included.

## Evaluation finding

The challenger wins validation Brier (0.1968 vs baseline 0.2033), but loses on the held-out test set (0.2009 vs 0.1879; lower is better). This result does not support a general improvement claim or automatic production promotion. Test cohort: 203 mature historically approved applications. Policy comparisons remain synthetic, selected using validation only and separated by currency.

## Delivery evidence

- Initial hosted CI passed: https://github.com/ezhilan03/underwriting-payment-risk/actions/runs/35660834971
- Final exposure-contract implementation: `3fd1a95a0182e15eb09a59b8d8c7fd38ae04959f`.
- Its hosted check: https://github.com/ezhilan03/underwriting-payment-risk/actions/runs/35661253934 — all three jobs passed. Downloaded artifact checksums and local/CI feature hashes agree.
- Local run: `artifacts/latest/manifest.json`, `report.json`, `warehouse-verification.json`.
- Local container evidence: `artifacts/verification/container.json`.

## Verified $0 GCP delivery

Dedicated project: `underwriting-risk-ez-2026`. Created September 21 with no linked billing account. Billing remained disabled before and after the complete verification run. The $0 deployment replaces the planned billing-enabled Cloud Run/GCS deployment; those components stay deferred and Terraform requires an explicit opt-in.

Real BigQuery Sandbox evidence (`artifacts/verification/sandbox.json`):

- Six source tables loaded, including 13,806 accepted raw events. Every field of every loaded row was read back and matched by canonical hash, not just row count.
- Five dbt models and seventeen data tests passed on BigQuery.
- BigQuery cohort marts exactly match the local DuckDB result.
- Raw events restored from BigQuery into a new local ledger reproduce features, outcomes and exposure snapshots.
- About 8.2 MB of source logical storage. Tables expire after 60 days; repository artifacts remain the durable reproduction source.
- No Cloud Run compute, GCS buckets, Artifact Registry images or scheduler deployed. Model training/orchestration runs locally; BigQuery executes warehouse transformations.

This completes the requested zero-dollar demo scope. A continuously running cloud application and durable GCS backup are deferred upgrades, not delivered claims. See `docs/SANDBOX.md` for rerun/expiry instructions.

