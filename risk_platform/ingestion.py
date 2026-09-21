"""Append-only delivery ledger with transactional conflict detection."""
import hashlib
import json
import math
import sqlite3
from datetime import datetime


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def validate(row):
    if not isinstance(row, dict):
        raise ValueError("record must be an object")
    kind = row.get("kind")
    if kind not in {"report", "enrollment", "payment", "return", "collection"}:
        raise ValueError("unknown kind")
    for key in ("source_id", "consumer_id"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(f"missing {key}")
    if row.get("schema_version") != 1 or type(row.get("version")) is not int or row["version"] < 1:
        raise ValueError("unsupported schema or invalid version")
    instant(row["received_at"])
    event_key = {"report": "effective_at", "enrollment": "decided_at", "payment": "attempted_at", "return": "occurred_at", "collection": "occurred_at"}[kind]
    event = instant(row[event_key])
    if instant(row["received_at"]) < event:
        raise ValueError("receipt precedes event")
    if kind != "report" and row["version"] != 1:
        raise ValueError("only vendor reports support replacement versions; money requires new adjustment event")
    if kind == "report":
        payload = row["payload"]
        if row["vendor_id"] == "vendor_a":
            debt, months = payload["debt_ratio"], payload["history_months"]
        elif row["vendor_id"] == "vendor_b":
            debt = None if payload["obligation_pct"] is None else payload["obligation_pct"] / 100
            months = None if payload["history_years"] is None else payload["history_years"] * 12
        else:
            raise ValueError("unknown vendor")
        for value in (debt, months):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError("non-finite vendor measurement")
        if debt is not None and not 0 <= debt <= 1:
            raise ValueError("invalid debt ratio")
        if months is not None and not 0 <= months <= 1200:
            raise ValueError("invalid history duration")
    else:
        if row["currency"] not in {"GBP", "USD"}:
            raise ValueError("unsupported currency")
        if kind == "enrollment":
            if type(row["original_approved"]) is not bool:
                raise ValueError("invalid historical decision")
            for key in ("report_id", "model_version", "policy_version"):
                if not isinstance(row[key], str) or not row[key]:
                    raise ValueError(f"missing {key}")
        else:
            amount_key = {"payment": "amount_minor", "return": "returned_amount_minor", "collection": "recovered_amount_minor"}[kind]
            if type(row[amount_key]) is not int or row[amount_key] <= 0:
                raise ValueError("money must be positive integer minor units")
            foreign = "application_id" if kind == "payment" else "payment_id"
            if not isinstance(row[foreign], str) or not row[foreign]:
                raise ValueError(f"missing {foreign}")
            if kind == "payment" and row["settlement_status"] not in {"settled", "failed"}:
                raise ValueError("invalid settlement status")
    return row


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS raw (kind TEXT, source_id TEXT, version INTEGER, hash TEXT, payload TEXT, PRIMARY KEY(kind, source_id, version))")
        self.db.execute("CREATE TABLE IF NOT EXISTS quarantine (hash TEXT PRIMARY KEY, reason TEXT, payload TEXT)")

    def ingest(self, deliveries):
        inserted = duplicates = invalid = 0
        with self.db:
            for row in deliveries:
                try:
                    validate(row)
                    body = canonical(row)
                except (ValueError, KeyError, TypeError) as exc:
                    try:
                        body = canonical(row)
                    except (ValueError, TypeError):
                        body = canonical({"invalid_record_repr": repr(row)})
                    key = hashlib.sha256(body.encode()).hexdigest()
                    self.db.execute("INSERT OR IGNORE INTO quarantine VALUES (?, ?, ?)", (key, str(exc), body))
                    invalid += 1
                    continue
                key = hashlib.sha256(body.encode()).hexdigest()
                prior = self.db.execute("SELECT hash FROM raw WHERE kind=? AND source_id=? AND version=?", (row["kind"], row["source_id"], row["version"])).fetchone()
                if prior:
                    if prior[0] != key:
                        raise ValueError("conflicting immutable source version: " + row["source_id"])
                    duplicates += 1
                else:
                    self.db.execute("INSERT INTO raw VALUES (?, ?, ?, ?, ?)", (row["kind"], row["source_id"], row["version"], key, body))
                    inserted += 1
        return dict(inserted=inserted, duplicates=duplicates, invalid_deliveries=invalid)

    def rows(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM raw ORDER BY kind, source_id, version")]

    def quarantined(self):
        return [dict(source_hash=h, reason=r, payload=p) for h, r, p in self.db.execute("SELECT hash, reason, payload FROM quarantine ORDER BY hash")]

    def close(self):
        self.db.close()
