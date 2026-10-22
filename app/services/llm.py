"""Run the model/tool conversation and return a proposed decision."""

import os

from app.domain.models import Decision
from app.services import data
from app.services.llm_fake import fake_llm


def decide(ticket: dict) -> Decision:
    mode = os.getenv("LLM_MODE", "fake")
    if mode != "fake":
        raise ValueError(f"Unsupported LLM_MODE: {mode}. Only fake is implemented.")

    tools = {
        "get_customer": data.get_customer,
        "get_invoices": data.get_invoices,
        "search_kb": data.search_kb,
    }
    tool_results = {}

    # Bound the conversation so a faulty model cannot request tools forever.
    for _ in range(8):
        response = fake_llm(ticket, tool_results)
        if isinstance(response, Decision):
            return response

        name = response["name"]
        if name not in tools:
            raise ValueError(f"Unknown tool: {name}")

        arguments = response["arguments"]
        if name in ("get_customer", "get_invoices"):
            if arguments.get("customer_id") != ticket["customer_id"]:
                raise ValueError("Tool customer_id must match the ticket customer")

        result = tools[name](**arguments)
        # Keep results JSON-compatible for a future real model adapter.
        if name == "get_invoices":
            result = [invoice.model_dump(mode="json") for invoice in result]
        tool_results[name] = result

    raise RuntimeError("Model exceeded the tool call limit")
