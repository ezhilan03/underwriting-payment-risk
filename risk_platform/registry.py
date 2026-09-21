"""Append-only reviewed model/policy promotions with optimistic concurrency."""
import json
import sqlite3
from .ingestion import canonical, digest


class Registry:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL)")

    def events(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM events ORDER BY sequence")]

    def promote(self, model_version, policy_version, reviewer, reason, expected_sequence, evidence_hash, rollback_to=None):
        if not all(isinstance(x, str) and x.strip() for x in (model_version, policy_version, reviewer, reason, evidence_hash)):
            raise ValueError("review metadata and evidence hash required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            history = self.events()
            if len(history) != expected_sequence:
                raise ValueError("stale registry version")
            if rollback_to is not None:
                if not 1 <= rollback_to <= len(history):
                    raise ValueError("invalid rollback target")
                target = history[rollback_to-1]
                if (target["model_version"], target["policy_version"]) != (model_version, policy_version):
                    raise ValueError("rollback must restore exact prior model and policy")
            event = dict(sequence=len(history)+1, model_version=model_version, policy_version=policy_version, reviewer=reviewer, reason=reason, evidence_hash=evidence_hash, rollback_to=rollback_to, prior_hash=digest(history[-1]) if history else None)
            self.db.execute("INSERT INTO events VALUES (?, ?, ?)", (event["sequence"], canonical(event), digest(event)))
            return event

    def close(self):
        self.db.close()
