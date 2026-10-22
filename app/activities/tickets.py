"""Temporal entry points for deciding, checking and executing ticket actions."""

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.domain.models import Decision
from app.domain.rules import check_decision
from app.services import data, llm
from app.services.helpdesk import FakeHelpdesk, TemporaryHelpdeskError


@activity.defn
def decide_ticket(ticket: dict) -> dict:
    """Ask the model for a proposal, without executing the action."""
    try:
        decision = Decision.model_validate(llm.decide(ticket))
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        # Invalid inputs, answers and exhausted tool loops need correction, not retries.
        raise ApplicationError(
            f"Cannot produce a decision: {error}",
            type="InvalidDecision",
            non_retryable=True,
        ) from error

    # Plain JSON-compatible values work with Temporal's default data converter.
    return decision.model_dump(mode="json")


@activity.defn
def check_ticket_decision(customer_id: str, decision_data: dict) -> dict:
    """Load invoice records and apply the independent business rules."""
    try:
        decision = Decision.model_validate(decision_data)
        invoices = data.get_invoices(customer_id)
        checked = check_decision(decision, customer_id, invoices)
    except (KeyError, TypeError, ValueError) as error:
        raise ApplicationError(
            f"Cannot check the decision: {error}",
            type="InvalidDecision",
            non_retryable=True,
        ) from error

    return checked.model_dump(mode="json")


@activity.defn
def execute_action(ticket_id: str, decision_data: dict) -> dict:
    """Execute the checked action after any required workflow approval."""
    try:
        decision = Decision.model_validate(decision_data)
        return FakeHelpdesk().execute(ticket_id, decision.action)
    except TemporaryHelpdeskError as error:
        # Let Temporal retry; the stable ticket ID prevents duplicate actions.
        raise ApplicationError(
            str(error), type="TemporaryHelpdeskError"
        ) from error
    except (TypeError, ValueError) as error:
        raise ApplicationError(
            f"Cannot execute the action: {error}",
            type="InvalidAction",
            non_retryable=True,
        ) from error
