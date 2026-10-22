import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.models import Action, ActionType
from app.services import helpdesk


@pytest.fixture
def service(tmp_path):
    return helpdesk.FakeHelpdesk(db_path=tmp_path / "helpdesk.sqlite3", failure_rate=0)


@pytest.mark.parametrize(
    "action",
    [
        Action(type=ActionType.REPLY, message="Reset your password", articles=["KB-1"]),
        Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=49),
        Action(type=ActionType.ESCALATE, reason="Needs human review"),
        Action(type=ActionType.CLOSE, reason="Not a support request"),
    ],
)
def test_actions_are_recorded(service, action):
    result = service.execute("T-1", action)
    assert result == {
        "ticket_id": "T-1",
        "status": "completed",
        "action": action.model_dump(mode="json", exclude_none=True),
    }


def test_temporary_failure_can_be_retried(service, monkeypatch):
    service.failure_rate = 0.5
    draws = iter([0.1, 0.9])
    monkeypatch.setattr(helpdesk.random, "random", lambda: next(draws))
    action = Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=49)

    with pytest.raises(helpdesk.TemporaryHelpdeskError):
        service.execute("T-1", action)

    with sqlite3.connect(service.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0

    assert service.execute("T-1", action)["status"] == "completed"


def test_completed_refund_survives_restart_without_repeating(service, monkeypatch):
    action = Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=49)
    first_result = service.execute("T-1", action)
    restarted = helpdesk.FakeHelpdesk(db_path=service.db_path, failure_rate=1)

    def unexpected_random():
        pytest.fail("Completed actions must be returned before simulating failures")

    monkeypatch.setattr(helpdesk.random, "random", unexpected_random)
    assert restarted.execute("T-1", action) == first_result
    with sqlite3.connect(service.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1


def test_same_ticket_cannot_execute_a_different_action(service):
    service.execute("T-1", Action(type=ActionType.REPLY, message="Hello"))
    with pytest.raises(ValueError, match="different action"):
        service.execute("T-1", Action(type=ActionType.CLOSE, reason="Done"))


def test_concurrent_refund_calls_record_one_action(service):
    action = Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=49)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.execute("T-1", action), range(4)))

    assert all(result == results[0] for result in results)
    with sqlite3.connect(service.db_path) as database:
        assert database.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1


@pytest.mark.parametrize(
    "action",
    [
        Action(type=ActionType.REPLY, message="  "),
        Action(type=ActionType.REFUND, invoice_id="INV-1001"),
        Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=-1),
        Action(type=ActionType.REFUND, invoice_id="INV-1001", amount_eur=float("nan")),
        Action(type=ActionType.ESCALATE),
        Action(type=ActionType.CLOSE, reason="  "),
    ],
)
def test_invalid_actions_are_permanent_errors(service, action):
    with pytest.raises(ValueError):
        service.execute("T-1", action)


@pytest.mark.parametrize("rate", [-0.1, 1.1, float("nan")])
def test_invalid_failure_rate_is_rejected(tmp_path, rate):
    with pytest.raises(ValueError, match="failure rate"):
        helpdesk.FakeHelpdesk(db_path=tmp_path / "helpdesk.sqlite3", failure_rate=rate)


def test_settings_are_read_from_environment(tmp_path, monkeypatch):
    database_path = tmp_path / "nested" / "helpdesk.sqlite3"
    monkeypatch.setenv("HELPDESK_DB_PATH", str(database_path))
    monkeypatch.setenv("HELPDESK_FAILURE_RATE", "1")
    service = helpdesk.FakeHelpdesk()
    assert service.db_path == database_path
    with pytest.raises(helpdesk.TemporaryHelpdeskError):
        service.execute("T-1", Action(type=ActionType.CLOSE, reason="Off topic"))
