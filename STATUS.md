# Underwriting / Payment Risk — implementation checkpoint

21 September 2026. Repository: https://github.com/ezhilan03/underwriting-payment-risk

## Implemented and locally verified

- 3,600 seeded synthetic applications, 4,954 payment attempts/exposure snapshots, 7,200 retrospective model scores and one deliberately invalid quarantined delivery.
- Two vendor schemas; original source versions, enrollment scores/decisions and synthetic residence/check provenance preserved. No residence or check-status fields enter the model feature allowlist.
- Duplicate-safe transactional ingestion, point-in-time features, frozen temporal training/validation labels, explicit maturity, separate currencies and attempted/settled/collected amounts.
- Logistic baseline and gradient-boosted challenger, calibration/ranking metrics, consumer-cluster Brier intervals, cold-start reporting and separate observed-versus-simulated policy economics.
- Fitted model artifact restoration reproduces every score. Portable accepted-source restoration reproduces features, outcomes and exposures. Registry promotion/rollback is explicitly an automated demonstration fixture.
- 30 application tests and four offline cloud-delivery tests pass. Five dbt models and seventeen data tests pass; fifteen direct SQL checks pass. Native local warehouse can be rebuilt after dbt without a table/view conflict.
- Terraform provider initialization, formatting and validation pass. Locked dependencies and a digest-pinned Python base image are included.

## Evaluation finding

The challenger wins validation Brier (0.1968 vs baseline 0.2033), but loses on the held-out test set (0.2009 vs 0.1879; lower is better). This result does not support a general improvement claim or automatic production promotion. Test cohort: 203 mature historically approved applications. Policy comparisons remain synthetic, selected using validation only and separated by currency.

## Delivery evidence

- Initial hosted CI passed: https://github.com/ezhilan03/underwriting-payment-risk/actions/runs/35660834971
- Final exposure-contract implementation: `3fd1a95a0182e15eb09a59b8d8c7fd38ae04959f`.
- Its hosted check: https://github.com/ezhilan03/underwriting-payment-risk/actions/runs/35661253934 — all three jobs passed. Downloaded artifact checksums and local/CI feature hashes agree.
- Local run: `artifacts/latest/manifest.json`, `report.json`, `warehouse-verification.json`.
- Local container evidence: `artifacts/verification/container.json`.

## Remaining to finish GCP delivery

The user has not yet selected a GCP deployment project or spending target. Existing CLI authentication and GitHub access work as of September 21; the earlier sandbox restriction is resolved. No GCP resources or billing configuration have been changed for this project.

Once the target is selected: inspect billing/APIs/permissions, review and apply Terraform, publish the digest-pinned image, execute the job, verify GCS generations/checksums and BigQuery/dbt results, perform source restoration from actual GCS objects, and confirm no job remains running. Only then mark cloud delivery complete. See `docs/GCP.md`.

## Session usage boundary

The user requested a warning and stop halfway through the token allowance. The visible tool reports account-wide percentage usage rather than exact tokens: warn at 45%, stop at 50%, checking between milestones. September 21 starting usage was 2%. No automatic continuation is authorized past the stop point.
