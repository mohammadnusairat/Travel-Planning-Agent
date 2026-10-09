from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class Constraint(BaseModel):
    name: str
    value: Any
    is_hard: bool = True


class Booking(BaseModel):
    booking_id: str
    type: str
    details: Dict[str, Any]
    cost: float
    dependencies: List[str] = Field(default_factory=list)


class ConstraintViolation(BaseModel):
    rule: str                      # e.g. "time_overlap", "budget_exceeded"
    category: str                  # "budget" | "temporal" | "dependency" | "accessibility"
    message: str
    booking_ids: List[str] = Field(default_factory=list)
    severity: str = "error"        # "error" invalidates the itinerary; "warning" is advisory
