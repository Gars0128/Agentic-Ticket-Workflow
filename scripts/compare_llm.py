"""Run the example tickets through the fake and the real model and print a table.

Real model settings come from the environment (LLM_MODEL, ANTHROPIC_API_KEY,
ANTHROPIC_BASE_URL), e.g. a local Ollama:
  ANTHROPIC_BASE_URL=http://localhost:11434 ANTHROPIC_API_KEY=ollama LLM_MODEL=qwen3:4b \
  python scripts/compare_llm.py
"""

import json
import os

from app.domain.rules import check_decision
from app.services import data, llm


def run(ticket: dict, mode: str) -> str:
    os.environ["LLM_MODE"] = mode
    try:
        proposed = llm.decide(ticket)
    except Exception as error:  # the workflow would escalate these
        return f"error: {error}"
    checked = check_decision(proposed, ticket["customer_id"], data.get_invoices(ticket["customer_id"]))
    action = checked.action
    detail = action.articles or action.invoice_id or action.reason or ""
    if action.amount_eur:
        detail = f"{detail} {action.amount_eur} EUR"
    return f"{checked.category.value}/{checked.priority.value} {action.type.value} {detail}"


print("| Ticket | Fake | Real |\n| --- | --- | --- |")
for ticket in json.loads((data.DATA_DIR / "tickets.json").read_text(encoding="utf-8")):
    print(f"| {ticket['ticket_id']} | {run(ticket, 'fake')} | {run(ticket, 'anthropic')} |")
