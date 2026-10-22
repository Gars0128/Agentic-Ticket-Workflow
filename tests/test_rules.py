import pytest

from app.domain.models import (
    Action, ActionType, Category, Decision, Invoice, Priority,
)
from app.domain.rules import check_decision, needs_approval

INVOICES = [
    Invoice(invoice_id="INV-1001", customer_id="C-100", month="2026-09", amount_eur=49.0, status="paid"),
    Invoice(invoice_id="INV-3001", customer_id="C-300", month="2026-09", amount_eur=990.0, status="paid"),
]


def make(action: Action) -> Decision:
    return Decision(category=Category.BILLING, priority=Priority.NORMAL, action=action)


def refund(invoice_id: str | None, amount: float | None) -> Decision:
    return make(Action(type=ActionType.REFUND, invoice_id=invoice_id, amount_eur=amount))


def test_valid_refund_passes():
    d = refund("INV-1001", 49.0)
    assert check_decision(d, "C-100", INVOICES) == d


def test_refund_larger_than_invoice_is_escalated():
    result = check_decision(refund("INV-1001", 5000.0), "C-100", INVOICES)
    assert result.action.type == ActionType.ESCALATE
    assert "exceeds" in result.action.reason


def test_refund_of_other_customers_invoice_is_escalated():
    result = check_decision(refund("INV-3001", 10.0), "C-100", INVOICES)
    assert result.action.type == ActionType.ESCALATE
    assert "another customer" in result.action.reason


def test_refund_unknown_invoice_is_escalated():
    result = check_decision(refund("INV-9999", 10.0), "C-100", INVOICES)
    assert result.action.type == ActionType.ESCALATE


@pytest.mark.parametrize("amount", [0, -5.0, None])
def test_refund_bad_amount_is_escalated(amount):
    result = check_decision(refund("INV-1001", amount), "C-100", INVOICES)
    assert result.action.type == ActionType.ESCALATE


def test_reply_without_message_is_escalated():
    d = make(Action(type=ActionType.REPLY, message="  "))
    assert check_decision(d, "C-100", INVOICES).action.type == ActionType.ESCALATE


def test_valid_reply_passes():
    d = make(Action(type=ActionType.REPLY, message="Reset it here", articles=["KB-1"]))
    assert check_decision(d, "C-200", INVOICES) == d


def test_close_without_reason_is_escalated():
    d = make(Action(type=ActionType.CLOSE))
    assert check_decision(d, "C-200", INVOICES).action.type == ActionType.ESCALATE


def test_only_refund_needs_approval():
    assert needs_approval(refund("INV-1001", 49.0)) is True
    assert needs_approval(make(Action(type=ActionType.REPLY, message="hi"))) is False