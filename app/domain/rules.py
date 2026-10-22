from app.domain.models import Action, ActionType, Decision, Invoice


def _escalate(decision: Decision, reason: str) -> Decision:
    """Replaces a decision with an escalation to a human."""
    return Decision(
        category=decision.category,
        priority=decision.priority,
        action=Action(type=ActionType.ESCALATE, reason=reason),
    )


def check_decision(
    decision: Decision, customer_id: str, invoices: list[Invoice]
) -> Decision:
    """Checks the model's decision. Rule violations -> escalation."""
    action = decision.action

    if action.type == ActionType.REPLY:
        # Replies require a non-blank message.
        if not action.message or not action.message.strip():
            # Missing, empty, or whitespace-only reply.
            return _escalate(decision, "Invalid decision: reply has no message")

    elif action.type in (ActionType.ESCALATE, ActionType.CLOSE):
        # Escalations and closures require a non-blank reason.
        if not action.reason or not action.reason.strip():
            # Missing, empty, or whitespace-only reason.
            return _escalate(decision, f"Invalid decision: {action.type.value} has no reason")

    elif action.type == ActionType.REFUND:
        # Refunds require an invoice reference and a valid amount.
        if action.invoice_id is None or action.amount_eur is None:
            # Missing invoice ID or refund amount.
            return _escalate(decision, "Invalid refund: invoice or amount missing")

        invoice = next((i for i in invoices if i.invoice_id == action.invoice_id), None)
        
        if invoice is None:
            # Invoice not found in the supplied records.
            return _escalate(decision, f"Invalid refund: invoice {action.invoice_id} not found")
        
        if invoice.customer_id != customer_id:
            # Refund targets another customer's invoice.
            return _escalate(decision, "Invalid refund: invoice belongs to another customer")
        
        if action.amount_eur <= 0:
            # Zero or negative refund amount.
            return _escalate(decision, "Invalid refund: amount must be positive")
        
        if action.amount_eur > invoice.amount_eur:
            # Refund exceeds the invoice amount.
            return _escalate(decision, "Invalid refund: amount exceeds invoice")

    return decision


def needs_approval(decision: Decision) -> bool:
    """A return always requires confirmation by a human."""
    return decision.action.type == ActionType.REFUND

