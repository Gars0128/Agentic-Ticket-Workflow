"""Keyword-based stand-in for an LLM, covering the example tickets offline."""

import re

from app.domain.models import Action, ActionType, Category, Decision, Priority


def fake_llm(ticket: dict, tool_results: dict) -> dict | Decision:
    """Return a tool request or a decision based on results collected so far."""
    # decide() calls this again after each tool result; no state is kept here.
    customer_id = ticket["customer_id"]
    ticket_text = f"{ticket['subject']} {ticket['body']}".lower()

    # A missing result means "not called yet"; None means "customer not found".
    if "get_customer" not in tool_results:
        return {
            "name": "get_customer",
            "arguments": {"customer_id": customer_id},
        }

    customer = tool_results["get_customer"]

    if customer is None:
        return Decision(
            category=Category.OTHER,
            priority=Priority.NORMAL,
            action=Action(
                type=ActionType.ESCALATE,
                reason="Customer not found",
            ),
        )

    if "refund" in ticket_text or "charged twice" in ticket_text:
        if "get_invoices" not in tool_results:
            return {
                "name": "get_invoices",
                "arguments": {"customer_id": customer_id},
            }

        invoices = tool_results["get_invoices"]
        paid_invoices = [
            invoice for invoice in invoices if invoice["status"] == "paid"
        ]

        # Recognize amounts such as "12.34 EUR" or "12.34€".
        requested_amount = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(?:eur\b|€)", ticket_text
        )
        refund_invoice = None
        refund_amount = None

        if requested_amount and paid_invoices:
            # The fake maps explicit amounts to the first paid invoice.
            # Keep even excessive amounts: rules.py must reject unsafe proposals.
            refund_invoice = paid_invoices[0]
            refund_amount = float(requested_amount[1])

        elif "charged twice" in ticket_text:
            # shortcut: matching month and amount imply a duplicate in the examples;
            # real billing would need payment transaction IDs to confirm this.
            for index, candidate in enumerate(paid_invoices):
                has_matching_invoice = any(
                    previous["month"] == candidate["month"]
                    and previous["amount_eur"] == candidate["amount_eur"]
                    for previous in paid_invoices[:index]
                )

                if has_matching_invoice:
                    refund_invoice = candidate
                    refund_amount = candidate["amount_eur"]
                    break

        if refund_invoice is None:
            action = Action(
                type=ActionType.ESCALATE,
                reason="Refund needs manual review: no clear invoice or amount",
            )

        else:
            action = Action(
                type=ActionType.REFUND,
                invoice_id=refund_invoice["invoice_id"],
                amount_eur=refund_amount,
            )

        return Decision(category=Category.BILLING, priority=Priority.NORMAL, action=action)

    priority = Priority.NORMAL
    escalate_outage = False

    # For non-refund requests, suspension takes precedence over text keywords.
    if customer["status"] == "suspended":
        category = Category.ACCOUNT
        search_query = "suspended accounts overdue"

    elif "password" in ticket_text or "log in" in ticket_text:
        category = Category.ACCOUNT
        search_query = "reset password"

    elif "api" in ticket_text or "outage" in ticket_text:
        category = Category.TECHNICAL
        search_query = "service status outages"
        priority = Priority.URGENT if "urgent" in ticket_text else Priority.HIGH

        # For this fake scenario, enterprise outages need a support specialist.
        escalate_outage = customer["plan"] == "enterprise"

    elif "pizza" in ticket_text or "lunch" in ticket_text:
        return Decision(
            category=Category.OTHER,
            priority=Priority.LOW,
            action=Action(type=ActionType.CLOSE, reason="Not a support request"),
        )

    else:
        return Decision(
            category=Category.OTHER,
            priority=Priority.NORMAL,
            action=Action(type=ActionType.ESCALATE, reason="No matching fake scenario"),
        )

    if "search_kb" not in tool_results:
        return {
            "name": "search_kb",
            "arguments": {"query": search_query},
        }

    articles = tool_results["search_kb"]

    if not articles:
        action = Action(type=ActionType.ESCALATE, reason="No matching instructions found")

    elif escalate_outage:
        action = Action(
            type=ActionType.ESCALATE,
            reason=f"Enterprise service outage needs human review. {articles[0]['text']}",
        )

    else:
        # search_kb returns the best match first.
        action = Action(
            type=ActionType.REPLY,
            message=articles[0]["text"],
            articles=[articles[0]["article_id"]],
        )

    return Decision(category=category, priority=priority, action=action)
