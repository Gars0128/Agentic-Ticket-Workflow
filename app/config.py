import os

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TASK_QUEUE = os.getenv("TASK_QUEUE", "tickets")


def workflow_id(ticket_id: str) -> str:
    # One workflow per ticket: the same ticket always maps to the same ID.
    return f"ticket-{ticket_id}"
