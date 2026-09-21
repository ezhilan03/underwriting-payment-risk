"""Knowledge-time feature snapshots and explicit maturity-aware outcome windows."""
from collections import defaultdict
from datetime import timedelta
from .ingestion import instant, digest, canonical


def partition(decided_at):
    date = instant(decided_at).date().isoformat()
    # 61+ day embargoes ensure labels are available before next partition.
    if "2024-01-01" <= date < "2024-05-01":
        return "train"
    if "2024-07-01" <= date < "2024-08-01":
        return "validation"
    if "2024-10-01" <= date < "2024-11-01":
        return "test"
    return "censored"


def normalize(rows):
    reports = [r for r in rows if r["kind"] == "report"]
    enrollments = {r["source_id"]: r for r in rows if r["kind"] == "enrollment"}
    payments, returns, collections, quarantine = {}, [], [], []

    def reject(row, reason):
        quarantine.append(dict(source_hash=digest(row), reason=reason, payload=canonical(row)))

    for row in rows:
        if row["kind"] != "payment":
            continue
        enrollment = enrollments.get(row["application_id"])
        if not enrollment or row["consumer_id"] != enrollment["consumer_id"] or row["currency"] != enrollment["currency"]:
            reject(row, "payment relationship or currency mismatch")
        elif not enrollment["original_approved"] or instant(row["attempted_at"]) < instant(enrollment["decided_at"]):
            reject(row, "payment precedes decision or belongs to rejected enrollment")
        else:
            payments[row["source_id"]] = row
    returned, recovered = defaultdict(int), defaultdict(int)
    # Process causally by received time so a collection cannot spend a future return.
    for row in sorted((r for r in rows if r["kind"] in {"return", "collection"}), key=lambda r: (instant(r["received_at"]), r["kind"] != "return", r["source_id"])):
        payment = payments.get(row["payment_id"])
        if not payment or payment["settlement_status"] != "settled" or row["consumer_id"] != payment["consumer_id"] or row["currency"] != payment["currency"]:
            reject(row, "event relationship, settlement or currency mismatch")
            continue
        if instant(row["occurred_at"]) < instant(payment["attempted_at"]):
            reject(row, "event precedes payment")
            continue
        pid = row["payment_id"]
        if row["kind"] == "return":
            if returned[pid] + row["returned_amount_minor"] > payment["amount_minor"]:
                reject(row, "returns exceed settled amount")
                continue
            returned[pid] += row["returned_amount_minor"]
            returns.append(row)
        else:
            if recovered[pid] + row["recovered_amount_minor"] > returned[pid]:
                reject(row, "recovery exceeds known returned balance")
                continue
            recovered[pid] += row["recovered_amount_minor"]
            collections.append(row)
    return dict(reports=reports, enrollments=list(enrollments.values()), payments=list(payments.values()), returns=returns, collections=collections), quarantine


def features(entities):
    reports, payments, returns = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in entities["reports"]:
        reports[r["source_id"]].append(r)
    for r in entities["payments"]:
        payments[(r["consumer_id"], r["currency"])].append(r)
    for r in entities["returns"]:
        returns[(r["consumer_id"], r["currency"])].append(r)
    result = []
    for e in sorted(entities["enrollments"], key=lambda r: r["source_id"]):
        cutoff = instant(e["decided_at"])
        available = [r for r in reports[e["report_id"]] if r["consumer_id"] == e["consumer_id"] and instant(r["effective_at"]) <= cutoff and instant(r["received_at"]) <= cutoff]
        report = max(available, key=lambda r: (instant(r["effective_at"]), r["version"], instant(r["received_at"])), default=None)
        debt = months = None
        if report:
            p = report["payload"]
            if report["vendor_id"] == "vendor_a":
                debt, months = p["debt_ratio"], p["history_months"]
            else:
                debt = None if p["obligation_pct"] is None else p["obligation_pct"] / 100
                months = None if p["history_years"] is None else round(p["history_years"] * 12)
        key = (e["consumer_id"], e["currency"])
        result.append(dict(application_id=e["source_id"], consumer_id=e["consumer_id"], decided_at=e["decided_at"], currency=e["currency"], report_id=report["source_id"] if report else None, report_version=report["version"] if report else None, report_effective_at=report["effective_at"] if report else None, report_received_at=report["received_at"] if report else None, history_missing=int(debt is None or months is None), debt_ratio=debt, history_months=months, prior_payments=sum(instant(p["attempted_at"]) < cutoff and instant(p["received_at"]) <= cutoff for p in payments[key]), prior_returns=sum(instant(r["occurred_at"]) < cutoff and instant(r["received_at"]) <= cutoff for r in returns[key]), original_approved=e["original_approved"], split=partition(e["decided_at"])))
    return result


def outcomes(entities, as_of, return_days=30, collection_days=60):
    if not 0 < return_days <= collection_days:
        raise ValueError("require 0 < return window <= collection window")
    cutoff = instant(as_of)
    payments, returns, collections = defaultdict(list), defaultdict(list), defaultdict(list)
    for p in entities["payments"]:
        payments[p["application_id"]].append(p)
    for row in entities["returns"]:
        returns[row["payment_id"]].append(row)
    for row in entities["collections"]:
        collections[row["payment_id"]].append(row)
    result = []
    for e in sorted(entities["enrollments"], key=lambda r: r["source_id"]):
        all_payments = payments[e["source_id"]]
        # Synthetic contract: all attempts occur within three days of enrollment.
        horizon = max([instant(e["decided_at"])+timedelta(days=3)] + [instant(p["attempted_at"]) for p in all_payments]) + timedelta(days=collection_days)
        row = dict(application_id=e["source_id"], consumer_id=e["consumer_id"], currency=e["currency"], decided_at=e["decided_at"], mature=horizon <= cutoff, observed=e["original_approved"], attempted_minor=0, settled_minor=0, returned_minor=0, recovered_minor=0, outstanding_minor=0, return_count=0, payment_count=0)
        for p in all_payments:
            attempted = instant(p["attempted_at"])
            if attempted > cutoff or instant(p["received_at"]) > cutoff:
                continue
            row["payment_count"] += 1
            row["attempted_minor"] += p["amount_minor"]
            if p["settlement_status"] != "settled":
                continue
            row["settled_minor"] += p["amount_minor"]
            included_returns = [r for r in returns[p["source_id"]] if instant(r["occurred_at"]) <= attempted+timedelta(days=return_days) and instant(r["received_at"]) <= cutoff]
            returned = sum(r["returned_amount_minor"] for r in included_returns)
            recovered = sum(r["recovered_amount_minor"] for r in collections[p["source_id"]] if instant(r["occurred_at"]) <= attempted+timedelta(days=collection_days) and instant(r["received_at"]) <= cutoff)
            # A recovery against a return outside the chosen label window is excluded.
            recovered = min(returned, recovered)
            row["returned_minor"] += returned
            row["recovered_minor"] += recovered
            row["return_count"] += int(returned > 0)
        row["outstanding_minor"] = row["returned_minor"] - row["recovered_minor"]
        result.append(row)
    return result
