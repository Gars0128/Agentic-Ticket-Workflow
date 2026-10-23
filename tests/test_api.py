"""API tests against Temporal's test environment, with fake activities."""

import httpx
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.api.main import app
from app.config import TASK_QUEUE, workflow_id
from app.workflows.ticket import TicketWorkflow
from tests.test_workflow import REFUND, REPLY, TICKET, fake_activities


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        app.state.client = env.client  # used instead of a real Temporal server
        yield env


def api():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_ticket_is_processed_once(env):
    activities, _ = fake_activities(REPLY)
    async with Worker(env.client, task_queue=TASK_QUEUE, workflows=[TicketWorkflow], activities=activities):
        async with api() as client:
            first = await client.post("/tickets", json=TICKET)
            second = await client.post("/tickets", json=TICKET)
            assert first.status_code == 202
            assert first.json() == {"ticket_id": "T-1", "workflow_id": "ticket-T-1", "created": True}
            assert second.json()["created"] is False

            await env.client.get_workflow_handle(workflow_id("T-1")).result()
            ticket = (await client.get("/tickets/T-1")).json()
            assert ticket["step"] == "done"
            assert ticket["result"]["action"]["type"] == "reply"

            # A finished ticket is not started again either.
            third = await client.post("/tickets", json=TICKET)
            assert third.json()["created"] is False

            # Only refunds that are waiting can be approved.
            approval = await client.post("/tickets/T-1/approval", json={"approved": True})
            assert approval.status_code == 409


async def test_refund_approval_through_api(env):
    activities, _ = fake_activities(REFUND)
    ticket = {**TICKET, "ticket_id": "T-2"}
    async with Worker(env.client, task_queue=TASK_QUEUE, workflows=[TicketWorkflow], activities=activities):
        async with api() as client:
            await client.post("/tickets", json=ticket)
            # Read the step without letting the test server skip the 24 hours.
            with env.auto_time_skipping_disabled():
                while (await client.get("/tickets/T-2")).json()["step"] != "waiting_for_approval":
                    pass

            approval = await client.post("/tickets/T-2/approval", json={"approved": True})
            assert approval.status_code == 200

            result = await env.client.get_workflow_handle(workflow_id("T-2")).result()
            assert result["action"]["type"] == "refund"


async def test_unknown_ticket_returns_404(env):
    async with api() as client:
        assert (await client.get("/tickets/T-404")).status_code == 404
