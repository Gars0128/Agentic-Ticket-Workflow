from enum import Enum

from pydantic import BaseModel, Field

#1
class Invoice(BaseModel):
    invoice_id: str
    customer_id: str
    month: str
    amount_eur: float
    status: str

#3
class Category(str, Enum):
    ACCOUNT = "account"
    BILLING = "billing"
    TECHNICAL = "technical"
    OTHER = "other"

#3
class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

#3(4)
class ActionType(str, Enum):
    REPLY = "reply"
    REFUND = "refund"
    ESCALATE = "escalate"
    CLOSE = "close"

#3
class Action(BaseModel):
    type: ActionType
    message: str | None = None          # for reply
    articles: list[str] = Field(default_factory=list)  # for reply
    invoice_id: str | None = None       # for refund
    amount_eur: float | None = None     # for refund
    reason: str | None = None           # for escalate / close

#2
class Decision(BaseModel):
    category: Category
    priority: Priority
    action: Action


