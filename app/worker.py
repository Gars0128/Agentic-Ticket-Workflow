"""Temporal worker: runs the ticket workflow and its activities."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.activities.tickets import check_ticket_decision, decide_ticket, execute_action
from app.config import TASK_QUEUE, TEMPORAL_HOST
from app.logs import setup_logging
from app.workflows.ticket import TicketWorkflow


async def main() -> None:
    setup_logging()
    client = await Client.connect(TEMPORAL_HOST)

    # The activities are normal (non-async) functions, so they run in threads.
    with ThreadPoolExecutor(max_workers=10) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[TicketWorkflow],
            activities=[decide_ticket, check_ticket_decision, execute_action],
            activity_executor=executor,
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
