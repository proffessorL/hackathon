from enum import Enum
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field

class DirectiveType(str, Enum):
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"

class SolarReductionAdjustment(BaseModel):
    hours: List[int]
    factor: float = Field(..., description="Fraction of solar that remains, e.g., 0.2 for 80% reduction")

class MinBatteryReserveAdjustment(BaseModel):
    hours: List[int]
    minimum_energy_kwh: float

class NoChargeWindowAdjustment(BaseModel):
    hours: List[int]

class NoDischargeWindowAdjustment(BaseModel):
    hours: List[int]

class MaxGridWindowAdjustment(BaseModel):
    hours: List[int]
    max_grid_kwh: float

StructuredAdjustment = Union[
    SolarReductionAdjustment,
    MinBatteryReserveAdjustment,
    NoChargeWindowAdjustment,
    NoDischargeWindowAdjustment,
    MaxGridWindowAdjustment,
    Dict[str, Any]
]

class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[Dict[str, Any]] = None
    explanation: str
