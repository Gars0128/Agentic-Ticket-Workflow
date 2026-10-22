import json
import sqlite3

import pytest
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.activities.tickets import check_ticket_decision, decide_ticket, execute_action
from app.domain.models import Action, ActionType, Category, Decision, Priority
from app.services import data, helpdesk, llm


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("HELPDESK_FAILURE_RATE", "0")
    monkeypatch.setenv("HELPDESK_DB_PATH", str(tmp_path / "helpdesk.sqlite3"))
    return ActivityEnvironment()


@pytest.fixture
def tickets():
    with open(data.DATA_DIR / "tickets.json", encoding="utf-8") as file:
        return {ticket["ticket_id"]: ticket for ticket in json.load(file)}


def refund_decision():
    return Decision(
        category=Category.BILLING,
        priority=Priority.NORMAL,
        action=Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=49),
    ).model_dump(mode="json")


def test_password_ticket_passes_through_all_activities(environment, tickets):
    ticket = tickets["T-1"]
    proposed = environment.run(decide_ticket, ticket)
    checked = environment.run(check_ticket_decision, ticket["customer_id"], proposed)
    result = environment.run(execute_action, ticket["ticket_id"], checked)

    assert checked["action"]["articles"] == ["KB-1"]
    assert result["status"] == "completed"
    assert result["action"]["type"] == "reply"
    assert "Forgot password" in result["action"]["message"]


def test_unsafe_refund_becomes_a_helpdesk_escalation(environment, tickets):
    ticket = tickets["T-6"]
    proposed = environment.run(decide_ticket, ticket)
    assert proposed["action"]["amount_eur"] == 5000

    checked = environment.run(check_ticket_decision, ticket["customer_id"], proposed)
    result = environment.run(execute_action, ticket["ticket_id"], checked)
    assert result["action"]["type"] == "escalate"
    assert "exceeds" in result["action"]["reason"]


def test_unknown_customer_returns_escalation(environment, tickets):
    decision = environment.run(decide_ticket, tickets["T-7"])
    assert decision["action"]["type"] == "escalate"
    assert decision["action"]["reason"] == "Customer not found"


def test_activity_failure_is_retryable_and_next_attempt_can_succeed(environment, monkeypatch):
    monkeypatch.setenv("HELPDESK_FAILURE_RATE", "0.5")
    draws = iter([0.1, 0.9])
    monkeypatch.setattr(helpdesk.random, "random", lambda: next(draws))

    with pytest.raises(ApplicationError) as failure:
        environment.run(execute_action, "T-2", refund_decision())
    assert failure.value.type == "TemporaryHelpdeskError"
    assert not failure.value.non_retryable

    # ActivityEnvironment does not schedule retries; exercise the next call explicitly.
    result = environment.run(execute_action, "T-2", refund_decision())
    assert result["action"]["type"] == "refund"
    assert result["status"] == "completed"


def test_repeated_refund_activity_returns_saved_result(environment, monkeypatch):
    first = environment.run(execute_action, "T-2", refund_decision())
    monkeypatch.setenv("HELPDESK_FAILURE_RATE", "1")
    assert environment.run(execute_action, "T-2", refund_decision()) == first

    with sqlite3.connect(helpdesk.FakeHelpdesk().db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1


@pytest.mark.parametrize("activity_function", [check_ticket_decision, execute_action])
def test_malformed_decision_is_not_retryable(environment, activity_function):
    with pytest.raises(ApplicationError) as failure:
        environment.run(activity_function, "C-100", {"category": "pizza"})
    assert failure.value.non_retryable


def test_invalid_ticket_is_not_retryable(environment):
    with pytest.raises(ApplicationError) as failure:
        environment.run(decide_ticket, {})
    assert failure.value.type == "InvalidDecision"
    assert failure.value.non_retryable


def test_exhausted_tool_loop_is_not_retryable(environment, tickets, monkeypatch):
    def broken_model(ticket):
        raise RuntimeError("Model exceeded the tool call limit")

    monkeypatch.setattr(llm, "decide", broken_model)
    with pytest.raises(ApplicationError) as failure:
        environment.run(decide_ticket, tickets["T-1"])
    assert failure.value.non_retryable


def test_model_connection_failure_remains_retryable(environment, tickets, monkeypatch):
    def unavailable_model(ticket):
        raise ConnectionError("Provider temporarily unavailable")

    monkeypatch.setattr(llm, "decide", unavailable_model)
    with pytest.raises(ConnectionError):
        environment.run(decide_ticket, tickets["T-1"])


def test_different_action_for_completed_ticket_is_not_retryable(environment):
    decision = refund_decision()
    environment.run(execute_action, "T-2", decision)
    decision["action"]["amount_eur"] = 10

    with pytest.raises(ApplicationError) as failure:
        environment.run(execute_action, "T-2", decision)
    assert failure.value.type == "InvalidAction"
    assert failure.value.non_retryable


@pytest.mark.asyncio
async def test_decision_survives_temporal_data_conversion(environment, tickets):
    proposed = environment.run(decide_ticket, tickets["T-1"])
    converter = DataConverter.default
    payloads = await converter.encode([proposed])
    decoded = await converter.decode(payloads, [dict])
    assert Decision.model_validate(decoded[0]).action.type == ActionType.REPLY
