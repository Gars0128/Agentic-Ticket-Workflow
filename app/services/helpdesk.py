"""Fake Helpdesk: fails at random and does each ticket's action only once."""

import json
import os
import random
import sqlite3
from contextlib import closing
from pathlib import Path

from app.domain.models import Action

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "helpdesk.sqlite3"


class TemporaryHelpdeskError(Exception):
    """A temporary failure: the caller may retry."""


class FakeHelpdesk:
    def __init__(self, db_path: str | Path | None = None, failure_rate: float | None = None):
        if failure_rate is None:
            failure_rate = float(os.getenv("HELPDESK_FAILURE_RATE", "0.3"))
        self.failure_rate = failure_rate
        self.db_path = Path(db_path or os.getenv("HELPDESK_DB_PATH", DEFAULT_DB))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS actions (ticket_id TEXT PRIMARY KEY, result TEXT)")

    def execute(self, ticket_id: str, action: Action) -> dict:
        """Do the action once per ticket. A repeat returns the saved result."""
        with closing(sqlite3.connect(self.db_path)) as db, db:
            # Lock the table so two retries running at once cannot both act.
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute(
                "SELECT result FROM actions WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
            if saved:
                return json.loads(saved[0])

            if random.random() < self.failure_rate:
                raise TemporaryHelpdeskError("Helpdesk temporarily unavailable")

            result = {
                "ticket_id": ticket_id,
                "status": "completed",
                "action": action.model_dump(mode="json", exclude_none=True),
            }
            # Saving the row is the fake side effect, e.g. "the refund was issued".
            db.execute("INSERT INTO actions VALUES (?, ?)", (ticket_id, json.dumps(result)))
        return result
