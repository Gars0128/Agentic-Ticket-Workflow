import json

import pytest

from app.domain.models import ActionType, Category, Priority
from app.domain.rules import check_decision, needs_approval
from app.services import data, llm


@pytest.fixture
def tickets(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    with open(data.DATA_DIR / "tickets.json", encoding="utf-8") as file:
        return {ticket["ticket_id"]: ticket for ticket in json.load(file)}


@pytest.mark.parametrize(
    "ticket_id, category, priority, action_type",
    [
        ("T-1", Category.ACCOUNT, Priority.NORMAL, ActionType.REPLY),
        ("T-2", Category.BILLING, Priority.NORMAL, ActionType.REFUND),
        ("T-3", Category.TECHNICAL, Priority.URGENT, ActionType.ESCALATE),
        ("T-4", Category.ACCOUNT, Priority.NORMAL, ActionType.REPLY),
        ("T-5", Category.OTHER, Priority.LOW, ActionType.CLOSE),
        ("T-6", Category.BILLING, Priority.NORMAL, ActionType.REFUND),
        ("T-7", Category.OTHER, Priority.NORMAL, ActionType.ESCALATE),
    ],
)
def test_example_decisions(tickets, ticket_id, category, priority, action_type):
    decision = llm.decide(tickets[ticket_id])
    assert decision.category == category
    assert decision.priority == priority
    assert decision.action.type == action_type


def test_password_conversation_uses_customer_and_article(tickets, monkeypatch):
    calls = []
    get_customer = data.get_customer

    def customer_tool(customer_id):
        calls.append(("get_customer", customer_id))
        return get_customer(customer_id)

    def kb_tool(query):
        calls.append(("search_kb", query))
        return [{"article_id": "KB-TEST", "text": "Instructions from the tool"}]

    monkeypatch.setattr(data, "get_customer", customer_tool)
    monkeypatch.setattr(data, "search_kb", kb_tool)
    decision = llm.decide(tickets["T-1"])
    assert calls == [("get_customer", "C-200"), ("search_kb", "reset password")]
    assert decision.action.message == "Instructions from the tool"
    assert decision.action.articles == ["KB-TEST"]


def test_duplicate_charge_refunds_matching_invoice(tickets):
    ticket = tickets["T-2"]
    decision = llm.decide(ticket)
    assert decision.action.invoice_id == "INV-1002"
    assert decision.action.amount_eur == 49.0
    assert check_decision(decision, ticket["customer_id"], data.get_invoices("C-100")) == decision
    assert needs_approval(decision)


def test_injected_refund_is_rejected_by_independent_rules(tickets):
    ticket = tickets["T-6"]
    proposed = llm.decide(ticket)
    assert proposed.action.amount_eur == 5000.0
    assert needs_approval(proposed)
    checked = check_decision(proposed, ticket["customer_id"], data.get_invoices("C-100"))
    assert checked.action.type == ActionType.ESCALATE
    assert "exceeds" in checked.action.reason


def test_suspended_customer_gets_suspension_instructions(tickets):
    decision = llm.decide(tickets["T-4"])
    assert decision.action.articles == ["KB-4"]
    assert "Pay the invoice" in decision.action.message


def test_missing_articles_escalate(tickets, monkeypatch):
    monkeypatch.setattr(data, "search_kb", lambda query: [])
    assert llm.decide(tickets["T-1"]).action.type == ActionType.ESCALATE


def test_missing_invoices_escalate(tickets, monkeypatch):
    monkeypatch.setattr(data, "get_invoices", lambda customer_id: [])
    assert llm.decide(tickets["T-2"]).action.type == ActionType.ESCALATE


def test_unknown_customer_stops_before_article_search(tickets, monkeypatch):
    def unexpected_search(query):
        pytest.fail("Unknown customers must not trigger article lookup")

    monkeypatch.setattr(data, "search_kb", unexpected_search)
    decision = llm.decide(tickets["T-7"])
    assert decision.action.reason == "Customer not found"


def test_unknown_tool_is_rejected(tickets, monkeypatch):
    monkeypatch.setattr(llm, "fake_llm", lambda ticket, results: {"name": "delete_all"})
    with pytest.raises(ValueError, match="Unknown tool"):
        llm.decide(tickets["T-1"])


def test_tool_cannot_request_another_customer(tickets, monkeypatch):
    monkeypatch.setattr(
        llm,
        "fake_llm",
        lambda ticket, results: {
            "name": "get_customer",
            "arguments": {"customer_id": "C-300"},
        },
    )
    with pytest.raises(ValueError, match="must match"):
        llm.decide(tickets["T-1"])


def test_repeated_tool_requests_are_bounded(tickets, monkeypatch):
    def repeating_model(ticket, results):
        return {"name": "get_customer", "arguments": {"customer_id": ticket["customer_id"]}}

    monkeypatch.setattr(llm, "fake_llm", repeating_model)
    with pytest.raises(RuntimeError, match="tool call limit"):
        llm.decide(tickets["T-1"])


def test_unsupported_mode_is_explicit(tickets, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "unknown")
    with pytest.raises(ValueError, match="Unsupported LLM_MODE"):
        llm.decide(tickets["T-1"])
