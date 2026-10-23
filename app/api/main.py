"""HTTP API: starts ticket workflows, reads their state and sends approvals."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.config import TASK_QUEUE, TEMPORAL_HOST, workflow_id
from app.logs import setup_logging
from app.workflows.ticket import TicketWorkflow

logger = logging.getLogger("api")


class TicketIn(BaseModel):
    ticket_id: str
    customer_id: str
    subject: str
    body: str


class ApprovalIn(BaseModel):
    approved: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    app.state.client = await Client.connect(TEMPORAL_HOST)
    yield


app = FastAPI(title="Northdesk tickets", lifespan=lifespan)


def log(message: str, ticket_id: str) -> None:
    logger.info(message, extra={"ticket_id": ticket_id, "workflow_id": workflow_id(ticket_id)})


async def read_status(handle: WorkflowHandle) -> dict:
    try:
        return await handle.query(TicketWorkflow.status)
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(404, "Ticket not found") from error
        raise


@app.post("/tickets", status_code=202)
async def create_ticket(ticket: TicketIn, request: Request) -> dict:
    client: Client = request.app.state.client
    wf_id = workflow_id(ticket.ticket_id)
    try:
        # Start and return at once; the worker processes the ticket in the background.
        await client.start_workflow(
            TicketWorkflow.run,
            ticket.model_dump(),
            id=wf_id,
            task_queue=TASK_QUEUE,
            # Never run the same ticket twice, even after the first run has finished.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        log("Duplicate ticket, returning the existing workflow", ticket.ticket_id)
        return {"ticket_id": ticket.ticket_id, "workflow_id": wf_id, "created": False}

    log("Ticket accepted", ticket.ticket_id)
    return {"ticket_id": ticket.ticket_id, "workflow_id": wf_id, "created": True}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request) -> dict:
    handle = request.app.state.client.get_workflow_handle(workflow_id(ticket_id))
    return await read_status(handle)


@app.post("/tickets/{ticket_id}/approval")
async def approve_refund(ticket_id: str, approval: ApprovalIn, request: Request) -> dict:
    handle = request.app.state.client.get_workflow_handle(workflow_id(ticket_id))
    status = await read_status(handle)
    if status["step"] != "waiting_for_approval":
        raise HTTPException(409, f"Ticket is not waiting for approval (step: {status['step']})")

    await handle.signal(TicketWorkflow.set_approval, approval.approved)
    log(f"Refund {'approved' if approval.approved else 'rejected'}", ticket_id)
    return {"ticket_id": ticket_id, "approved": approval.approved}
