"""Fake Helpdesk actions with temporary failures and persistent deduplication."""

import json
import math
import os
import random
import sqlite3
from contextlib import closing
from pathlib import Path

from app.domain.models import Action, ActionType


class TemporaryHelpdeskError(Exception):
    """A temporary service failure that the caller may retry."""


class FakeHelpdesk:
    def __init__(
        self,
        db_path: str | Path | None = None,
        failure_rate: float | None = None,
    ):
        self.failure_rate = (
            float(os.getenv("HELPDESK_FAILURE_RATE", "0.3"))
            if failure_rate is None
            else failure_rate
        )
        if not 0 <= self.failure_rate <= 1:
            raise ValueError("Helpdesk failure rate must be between 0 and 1")

        default_path = Path(__file__).resolve().parents[2] / "data" / "helpdesk.sqlite3"
        self.db_path = Path(
            db_path if db_path is not None else os.getenv("HELPDESK_DB_PATH", str(default_path))
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with closing(sqlite3.connect(self.db_path)) as database:
            with database:
                database.execute(
                    "CREATE TABLE IF NOT EXISTS actions ("
                    "ticket_id TEXT PRIMARY KEY, action TEXT NOT NULL, result TEXT NOT NULL)"
                )

    def execute(self, ticket_id: str, action: Action) -> dict:
        """Record one final action per ticket, or return its previous result."""
        if not ticket_id.strip():
            raise ValueError("Ticket ID must not be blank")

        if action.type == ActionType.REPLY:
            if not action.message or not action.message.strip():
                raise ValueError("Reply requires a non-blank message")

        elif action.type == ActionType.REFUND:
            if not action.invoice_id or not action.invoice_id.strip():
                raise ValueError("Refund requires an invoice ID")
            if (
                action.amount_eur is None
                or not math.isfinite(action.amount_eur)
                or action.amount_eur <= 0
            ):
                raise ValueError("Refund requires a positive finite amount")

        else:
            if not action.reason or not action.reason.strip():
                raise ValueError("Escalation or closure requires a non-blank reason")

        # Invoice ownership, refund limits and approval are checked before this call.
        action_data = action.model_dump(mode="json", exclude_none=True)
        action_json = json.dumps(action_data, sort_keys=True, allow_nan=False)

        with closing(sqlite3.connect(self.db_path)) as database:
            with database:
                # Serialize lookup and insert so concurrent retries cannot act twice.
                database.execute("BEGIN IMMEDIATE")
                previous = database.execute(
                    "SELECT action, result FROM actions WHERE ticket_id = ?", (ticket_id,)
                ).fetchone()

                if previous is not None:
                    if previous[0] != action_json:
                        raise ValueError("Ticket already completed with a different action")
                    return json.loads(previous[1])

                if random.random() < self.failure_rate:
                    raise TemporaryHelpdeskError("Helpdesk temporarily unavailable")

                result = {
                    "ticket_id": ticket_id,
                    "status": "completed",
                    "action": action_data,
                }
                # The record is the fake side effect; commit before returning success.
                database.execute(
                    "INSERT INTO actions (ticket_id, action, result) VALUES (?, ?, ?)",
                    (ticket_id, action_json, json.dumps(result)),
                )

        return result
