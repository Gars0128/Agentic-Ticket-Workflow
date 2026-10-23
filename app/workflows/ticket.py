"""The ticket workflow: decide -> check -> wait for approval (refunds) -> act.

A workflow must be deterministic: it only orchestrates. Everything that touches
files, the network or random numbers runs in activities.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

# The workflow only references the activities; their code runs in the worker,
# so they can skip Temporal's import sandbox.
with workflow.unsafe.imports_passed_through():
    from app.activities.tickets import check_ticket_decision, decide_ticket, execute_action

APPROVAL_TIMEOUT = timedelta(hours=24)

# The LLM can be briefly unavailable: a few retries, then the ticket is escalated.
LLM_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)
# The Helpdesk fails at random. Retrying is safe: it records one action per ticket.
HELPDESK_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)


def escalation(decision: dict | None, reason: str) -> dict:
    """A decision in the same shape as Decision.model_dump(), sent to a person."""
    decision = decision or {"category": "other", "priority": "normal"}
    return {
        "category": decision["category"],
        "priority": decision["priority"],
        "action": {"type": "escalate", "reason": reason},
    }


@workflow.defn
class TicketWorkflow:
    def __init__(self) -> None:
        # Stored on the workflow so GET /tickets/{id} can read them at any time.
        self.ticket_id = ""
        self.step = "started"
        self.decision: dict | None = None
        self.result: dict | None = None
        self.approved: bool | None = None  # None until the approval signal arrives

    @workflow.run
    async def run(self, ticket: dict) -> dict:
        self.ticket_id = ticket["ticket_id"]
        self.log("Workflow started")

        # 1. Decide: the agent proposes category, priority and action.
        self.step = "deciding"
        try:
            proposed = await workflow.execute_activity(
                decide_ticket,
                ticket,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=LLM_RETRY,
            )
        except ActivityError as error:
            # No usable answer, even after retries: a person takes over.
            proposed = escalation(None, f"No decision from the model: {error.cause}")

        # 2. Check: the rules in code have the last word, not the model.
        self.step = "checking"
        self.decision = await workflow.execute_activity(
            check_ticket_decision,
            args=[ticket["customer_id"], proposed],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=LLM_RETRY,
        )
        self.log(f"Decision checked: {self.decision['action']['type']}")

        # 3. Refunds wait for a person, at most 24 hours.
        if self.decision["action"]["type"] == "refund":
            self.step = "waiting_for_approval"
            try:
                await workflow.wait_condition(
                    lambda: self.approved is not None, timeout=APPROVAL_TIMEOUT
                )
            except TimeoutError:
                self.decision = escalation(self.decision, "No refund approval within 24 hours")
            else:
                if not self.approved:
                    self.decision = escalation(self.decision, "Refund rejected by a reviewer")
            self.log(f"Approval step finished: {self.decision['action']['type']}")

        # 4. Act through the Helpdesk.
        self.step = "acting"
        try:
            self.result = await workflow.execute_activity(
                execute_action,
                args=[self.ticket_id, self.decision],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=HELPDESK_RETRY,
            )
        except ActivityError as error:
            self.step = "failed"
            self.log(f"Action failed: {error.cause}")
            raise ApplicationError(f"Helpdesk action failed: {error.cause}") from error

        self.step = "done"
        self.log("Workflow finished")
        return self.result

    @workflow.signal
    def set_approval(self, approved: bool) -> None:
        """Sent by POST /tickets/{id}/approval."""
        self.approved = approved

    @workflow.query
    def status(self) -> dict:
        """Read by GET /tickets/{id}."""
        return {
            "ticket_id": self.ticket_id,
            "step": self.step,
            "decision": self.decision,
            "result": self.result,
        }

    def log(self, message: str) -> None:
        # workflow.logger adds the workflow ID and skips duplicates on replay.
        workflow.logger.info(message, extra={"ticket_id": self.ticket_id})
