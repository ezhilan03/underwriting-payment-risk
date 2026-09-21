"""Temporal model experiment; counterfactual policy results are labelled simulation."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from .ingestion import digest

FEATURES = ("debt_ratio", "history_months", "history_missing", "prior_returns", "prior_payments")


def matrix(rows):
    return np.array([[np.nan if r[k] is None else r[k] for k in FEATURES] for r in rows], dtype=float)


def metrics(y, p):
    if len(y) == 0:
        return dict(count=0, roc_auc=None, average_precision=None, brier=None, log_loss=None, calibration=[])
    calibration = []
    for low, high in zip(np.arange(0, 1, .2), np.arange(.2, 1.01, .2)):
        mask = (p >= low) & ((p <= high) if high > .99 else (p < high))
        calibration.append(dict(lower=round(float(low), 2), upper=round(float(high), 2), count=int(mask.sum()), predicted=float(p[mask].mean()) if mask.any() else None, observed=float(y[mask].mean()) if mask.any() else None))
    return dict(count=len(y), positive_count=int(y.sum()), roc_auc=float(roc_auc_score(y, p)) if len(set(y)) > 1 else None, average_precision=float(average_precision_score(y, p)) if len(set(y)) > 1 else None, brier=float(brier_score_loss(y, p)), log_loss=float(log_loss(y, p, labels=[0, 1])), calibration=calibration)


def bootstrap_brier(y, p, consumers, seed, repetitions=200):
    if not len(y):
        return None
    groups = defaultdict(list)
    for i, consumer in enumerate(consumers):
        groups[consumer].append(i)
    keys = sorted(groups)
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(repetitions):
        indices = [i for key in rng.choice(keys, len(keys), replace=True) for i in groups[key]]
        values.append(float(np.mean((y[indices] - p[indices]) ** 2)))
    return dict(method="consumer-cluster percentile bootstrap", repetitions=repetitions, confidence=.95, lower=float(np.quantile(values, .025)), upper=float(np.quantile(values, .975)))


def economics(rows, scores, threshold):
    """All amounts within one currency; fees/costs are illustrative assumptions."""
    approved = [row for row in rows if scores[row["application_id"]] <= threshold]
    attempted = sum(r["attempted_minor"] for r in approved)
    settled = sum(r["settled_minor"] for r in approved)
    returned = sum(r["returned_minor"] for r in approved)
    recovered = sum(r["recovered_minor"] for r in approved)
    payments = sum(r["payment_count"] for r in approved)
    returns = sum(r["return_count"] for r in approved)
    collected = settled-returned+recovered
    fee = sum((r["settled_minor"]-r["returned_minor"]+r["recovered_minor"]) * 2 // 100 for r in approved)
    cost = payments*25 + returns*150
    loss = returned-recovered
    return dict(eligible_count=len(rows), approval_count=len(approved), approval_rate=len(approved)/len(rows) if rows else None, payment_count=payments, return_count=returns, return_rate=returns/payments if payments else None, attempted_minor=attempted, settled_minor=settled, collected_minor=collected, returned_minor=returned, recovered_minor=recovered, uncollected_exposure_minor=loss, matured_loss_minor=loss, fee_revenue_minor=fee, costs_minor=cost, net_contribution_minor=fee-cost-loss)


def run_experiment(features, outcomes, latent, seed=42, training_outcomes=None, validation_outcomes=None, model_directory=None):
    if training_outcomes is None or validation_outcomes is None:
        raise ValueError("Frozen training and validation label snapshots are required")
    by_id = {r["application_id"]: r for r in outcomes}
    # Freeze labels at the time each experiment decision could actually be made.
    split_ids = {r["application_id"]: r["split"] for r in features}
    for split, snapshots in (("train", training_outcomes), ("validation", validation_outcomes)):
        if snapshots is not None:
            by_id.update({r["application_id"]: r for r in snapshots if split_ids[r["application_id"]] == split})
    observed = [r for r in features if by_id[r["application_id"]]["observed"] and by_id[r["application_id"]]["mature"]]
    sets = {split: [r for r in observed if r["split"] == split] for split in ("train", "validation", "test")}
    for split, rows in sets.items():
        if len(rows) < 20 or len({int(by_id[r["application_id"]]["return_count"] > 0) for r in rows}) < 2:
            raise ValueError(f"{split} requires >=20 observed mature rows and both outcomes")
    labels = lambda rows: np.array([int(by_id[r["application_id"]]["return_count"] > 0) for r in rows])
    models = {
        "baseline": make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), LogisticRegression(C=1.0, max_iter=1000, random_state=seed)),
        "challenger": make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), HistGradientBoostingClassifier(max_iter=80, max_leaf_nodes=7, l2_regularization=5, learning_rate=.05, early_stopping=False, random_state=seed)),
    }
    train = sets["train"]
    train_consumers = {r["consumer_id"] for r in train}
    evaluation, score_rows, all_scores, models_metadata = {}, [], {}, {}
    for name, model in models.items():
        model.fit(matrix(train), labels(train))
        evaluation[name] = {}
        for split in ("validation", "test"):
            rows = sets[split]
            y = labels(rows)
            p = model.predict_proba(matrix(rows))[:, 1]
            evaluation[name][split] = metrics(y, p)
            evaluation[name][split]["brier_interval"] = bootstrap_brier(y, p, [r["consumer_id"] for r in rows], seed)
            for cohort, mask in (("cold_start", np.array([r["consumer_id"] not in train_consumers for r in rows])), ("recurring", np.array([r["consumer_id"] in train_consumers for r in rows]))):
                evaluation[name][split][cohort] = metrics(y[mask], p[mask])
        predictions = model.predict_proba(matrix(features))[:, 1]
        all_scores[name] = {r["application_id"]: float(p) for r, p in zip(features, predictions)}
        score_rows.extend(dict(application_id=r["application_id"], model_version=name, feature_version="pit-v1", data_cutoff=r["decided_at"], scored_at="2025-02-01T00:00:00Z", score_context="retrospective_synthetic_experiment", risk_score=float(p), split=r["split"], consumer_seen_in_train=r["consumer_id"] in train_consumers) for r, p in zip(features, predictions))
        # Safe descriptive metadata, no executable pickle artifact.
        models_metadata[name] = dict(estimator=type(model.steps[-1][1]).__name__, parameters=model.steps[-1][1].get_params(), imputation_medians=model.steps[0][1].statistics_.tolist())
        if model_directory is not None:
            directory = Path(model_directory)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{name}.joblib"
            joblib.dump(model, path)
            # Load only the trusted artifact generated in this process, then compare
            # every score. The release manifest pins these executable model bytes.
            restored_model = joblib.load(path)
            if not np.array_equal(restored_model.predict_proba(matrix(features))[:, 1], predictions):
                raise ValueError("restored model predictions differ")
            models_metadata[name].update(artifact=f"models/{name}.joblib", artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), restored_predictions_identical=True)
        if name == "baseline":
            models_metadata[name]["standardized_coefficients"] = model.steps[-1][1].coef_[0].tolist()
            models_metadata[name]["intercept"] = model.steps[-1][1].intercept_.tolist()
    # Selection sees validation only; held-out results never choose a model or threshold.
    selected = min(models, key=lambda name: (evaluation[name]["validation"]["brier"], name))
    validation_rows = [by_id[r["application_id"]] for r in sets["validation"]]
    # Separate policy for each currency, no cross-currency sum used in selection.
    thresholds = {}
    for currency in sorted({r["currency"] for r in features}):
        rows = [r for r in validation_rows if r["currency"] == currency]
        if not rows:
            thresholds[currency] = .5
            continue
        candidates = [(.2, economics(rows, all_scores[selected], .2)), (.35, economics(rows, all_scores[selected], .35)), (.5, economics(rows, all_scores[selected], .5)), (.65, economics(rows, all_scores[selected], .65))]
        thresholds[currency] = max(candidates, key=lambda item: (item[1]["net_contribution_minor"], -item[0]))[0]
    simulation = defaultdict(lambda: dict(attempted_minor=0, settled_minor=0, returned_minor=0, recovered_minor=0, payment_count=0, return_count=0))
    for row in latent:
        agg = simulation[row["application_id"]]
        agg.update(application_id=row["application_id"], consumer_id=row["consumer_id"], currency=row["currency"])
        for key in ("attempted_minor", "settled_minor", "returned_minor", "recovered_minor"):
            agg[key] += row[key]
        agg["payment_count"] += 1
        agg["return_count"] += int(row["returned_minor"] > 0)
    policy = []
    for source in ("observed_approved_only", "synthetic_all_consumer_simulation"):
        eligible = [r for r in features if r["split"] == "test" and by_id[r["application_id"]]["mature"] and (source != "observed_approved_only" or by_id[r["application_id"]]["observed"])]
        for currency in sorted({r["currency"] for r in features}):
            rows = [(by_id if source == "observed_approved_only" else simulation)[r["application_id"]] for r in eligible if r["currency"] == currency]
            for name, threshold, comparison in (("baseline", .5, "same_policy"), ("challenger", .5, "same_policy"), (selected, thresholds[currency], "selected_model_validation_policy")):
                policy.append(dict(source=source, currency=currency, model=name, threshold=threshold, comparison=comparison, **economics(rows, all_scores[name], threshold)))
    return dict(seed=seed, feature_version="pit-v1", features=list(FEATURES), training_cutoff="2024-07-01T00:00:00Z", validation_cutoff="2024-10-01T00:00:00Z", outcome_as_of="2025-02-01T00:00:00Z", training_data_hash=digest([dict(feature=r, outcome=by_id[r["application_id"]]) for r in train]), training_count=len(train), selected_model=selected, selection_rule="minimum validation Brier; no test-driven retuning", selected_thresholds=thresholds, thresholds_rule="validation observed-approved net contribution by currency; fixed grid 0.2/0.35/0.5/0.65", models=models_metadata, evaluation=evaluation, policy=policy, assumptions=dict(fee_percent=2, attempt_cost_minor=25, return_cost_minor=150, loss_bearer="simulated platform absorbs uncollected principal", threshold_scope="historically approved validation subset only", uncertainty="consumer-cluster Brier intervals only; policy economics are point estimates", observational_limit="No observed outcomes for rejected consumers. Simulation is not causal evidence.")), score_rows
