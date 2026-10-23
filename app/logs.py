"""JSON-lines logging shared by the API, the workflow and the activities."""

import json
import logging

from temporalio import activity, workflow


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Temporal's loggers attach workflow details; the API passes them itself.
        temporal = (
            getattr(record, "temporal_workflow", None)
            or getattr(record, "temporal_activity", None)
            or {}
        )
        ticket_id = getattr(record, "ticket_id", None)
        workflow_id = getattr(record, "workflow_id", None) or temporal.get("workflow_id")
        if ticket_id:
            line["ticket_id"] = ticket_id
        if workflow_id:
            line["workflow_id"] = workflow_id
        return json.dumps(line)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    # Workflow details already go into separate JSON fields, not the message text.
    workflow.logger.workflow_info_on_message = False
    activity.logger.activity_info_on_message = False
