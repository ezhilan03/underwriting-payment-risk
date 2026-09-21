"""Deterministic synthetic deliveries. Latent counterfactuals stay separate."""
from datetime import datetime, timedelta, timezone
import math
import random


def timestamp(day):
    return day.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def generate(seed=42, applications=3600):
    rng = random.Random(seed)
    raw, latent = [], []
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(applications):
        day = start + timedelta(days=i % 365, hours=i % 12)
        # Recurring consumers and explicit new entrants in the held-out period.
        consumer = f"c-{i % 400}" if i % 3 else f"new-{i}"
        application = f"a-{i:05d}"
        currency = "GBP" if i % 5 else "USD"
        debt = round(rng.betavariate(2, 3), 4)
        months = rng.randint(0, 120)
        missing = rng.random() < .12
        p = 1 / (1 + math.exp(-(-3.2 + 4.0 * debt + 1.1 * (months < 12) + 2.2 * (debt > .65 and months < 30))))
        approved = rng.random() > (.13 + .45 * debt)
        report_id = f"report-{i:05d}"
        common = dict(consumer_id=consumer, schema_version=1)
        vendor_a = i % 2 == 0
        payload = {"debt_ratio": debt, "history_months": months} if vendor_a else {"obligation_pct": round(debt * 100, 2), "history_years": months / 12}
        if missing:
            payload = {key: None for key in payload}
        report = dict(common, kind="report", source_id=report_id, version=1, vendor_id="vendor_a" if vendor_a else "vendor_b", effective_at=timestamp(day-timedelta(days=2)), received_at=timestamp(day-timedelta(days=1)), payload=payload)
        # Some reports arrive too late to be available at enrollment.
        if i % 23 == 0:
            report["received_at"] = timestamp(day+timedelta(days=2))
        raw.append(report)
        if i % 13 == 0:
            corrected = dict(payload)
            corrected["debt_ratio" if vendor_a else "obligation_pct"] = .99 if vendor_a else 99.0
            raw.append(dict(report, version=2, received_at=timestamp(day+timedelta(days=20)), payload=corrected))
        raw.append(dict(common, kind="enrollment", source_id=application, version=1, decided_at=timestamp(day), received_at=timestamp(day), report_id=report_id, currency=currency, original_approved=approved, model_version="historical-v1", policy_version="historical-v1"))
        pay_count = rng.randint(1, 3)
        for j in range(pay_count):
            attempted = day + timedelta(days=j+1)
            payment_id = f"p-{i:05d}-{j}"
            amount = rng.randint(1000, 40000)
            settled = rng.random() >= .05
            returned = rng.random() < p and settled
            recovery = int(amount * rng.uniform(.05, .75)) if returned and rng.random() < .4 else 0
            latent.append(dict(application_id=application, consumer_id=consumer, currency=currency, payment_id=payment_id, attempted_minor=amount, settled_minor=amount if settled else 0, returned_minor=amount if returned else 0, recovered_minor=recovery, simulated=True))
            if not approved:
                continue
            raw.append(dict(common, kind="payment", source_id=payment_id, version=1, application_id=application, attempted_at=timestamp(attempted), received_at=timestamp(attempted), amount_minor=amount, currency=currency, settlement_status="settled" if settled else "failed"))
            if returned:
                event = attempted + timedelta(days=rng.randint(2, 28))
                raw.append(dict(common, kind="return", source_id=f"r-{payment_id}", version=1, payment_id=payment_id, occurred_at=timestamp(event), received_at=timestamp(event+timedelta(days=12 if i % 17 == 0 else 1)), reason="synthetic_insufficient_funds", returned_amount_minor=amount, currency=currency))
                if recovery:
                    recovered = attempted+timedelta(days=45)
                    raw.append(dict(common, kind="collection", source_id=f"k-{payment_id}", version=1, payment_id=payment_id, occurred_at=timestamp(recovered), received_at=timestamp(recovered), recovered_amount_minor=recovery, currency=currency))
    raw.extend(dict(row) for row in raw[::29])
    raw.append(dict(kind="payment", source_id="bad-negative", version=1, schema_version=1, consumer_id="bad", application_id="missing", attempted_at=timestamp(start), received_at=timestamp(start), amount_minor=-5, currency="GBP", settlement_status="settled"))
    rng.shuffle(raw)
    return raw, latent
