"""Workflow tests in Temporal's time-skipping test environment, with fake activities."""

import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.config import TASK_QUEUE
from app.workflows.ticket import TicketWorkflow

TICKET = {"ticket_id": "T-1", "customer_id": "C-200", "subject": "Hi", "body": "Help"}
REPLY = {
    "category": "account",
    "priority": "normal",
    "action": {"type": "reply", "message": "Use 'Forgot password'.", "articles": ["KB-1"]},
}
REFUND = {
    "category": "billing",
    "priority": "normal",
    "action": {"type": "refund", "invoice_id": "INV-1002", "amount_eur": 49.0},
}


def fake_activities(decision: dict, helpdesk_failures: int = 0):
    """Activities with the real names, but no LLM, files or random failures."""
    calls = {"execute_action": 0}

    @activity.defn(name="decide_ticket")
    async def decide_ticket(ticket: dict) -> dict:
        return decision

    @activity.defn(name="check_ticket_decision")
    async def check_ticket_decision(customer_id: str, proposed: dict) -> dict:
        return proposed

    @activity.defn(name="execute_action")
    async def execute_action(ticket_id: str, checked: dict) -> dict:
        calls["execute_action"] += 1
        if calls["execute_action"] <= helpdesk_failures:
            raise RuntimeError("Helpdesk temporarily unavailable")
        return {"ticket_id": ticket_id, "status": "completed", "action": checked["action"]}

    return [decide_ticket, check_ticket_decision, execute_action], calls


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def start(env, activities):
    worker = Worker(env.client, task_queue=TASK_QUEUE, workflows=[TicketWorkflow], activities=activities)
    handle = await env.client.start_workflow(
        TicketWorkflow.run, TICKET, id=str(uuid.uuid4()), task_queue=TASK_QUEUE
    )
    return worker, handle


async def test_ticket_is_answered(env):
    activities, _ = fake_activities(REPLY)
    worker, handle = await start(env, activities)
    async with worker:
        result = await handle.result()
        status = await handle.query(TicketWorkflow.status)

    assert result["action"]["type"] == "reply"
    assert status["step"] == "done"
    assert status["decision"] == REPLY


async def test_refund_is_approved(env):
    activities, _ = fake_activities(REFUND)
    worker, handle = await start(env, activities)
    async with worker:
        await handle.signal(TicketWorkflow.set_approval, True)
        result = await handle.result()

    assert result["action"]["type"] == "refund"


async def test_refund_is_rejected(env):
    activities, _ = fake_activities(REFUND)
    worker, handle = await start(env, activities)
    async with worker:
        await handle.signal(TicketWorkflow.set_approval, False)
        result = await handle.result()

    assert result["action"] == {"type": "escalate", "reason": "Refund rejected by a reviewer"}


async def test_refund_without_answer_for_24_hours_is_escalated(env):
    activities, _ = fake_activities(REFUND)
    worker, handle = await start(env, activities)
    async with worker:
        # The test server skips the 24 hours instead of waiting.
        result = await handle.result()

    assert result["action"] == {"type": "escalate", "reason": "No refund approval within 24 hours"}


async def test_failed_activity_succeeds_after_retry(env):
    activities, calls = fake_activities(REPLY, helpdesk_failures=2)
    worker, handle = await start(env, activities)
    async with worker:
        result = await handle.result()

    assert result["status"] == "completed"
    assert calls["execute_action"] == 3
