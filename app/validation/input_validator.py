import math
from typing import List, Tuple
from app.models.request import OptimizeRequest

class InputValidationError(Exception):
    """Raised when input scenario violates semantic validation rules."""
    pass

def validate_input_scenario(request: OptimizeRequest) -> None:
    """
    Validates semantic requirements for the input request.
    Raises InputValidationError if any constraint is violated.
    """
    # Operator notes check
    if not request.operator_notes or len(request.operator_notes) < 1 or len(request.operator_notes) > 3:
        raise InputValidationError("operator_notes must contain between 1 and 3 non-empty strings.")

    # Hours check
    if len(request.hours) != 24:
        raise InputValidationError(f"hours must contain exactly 24 entries, got {len(request.hours)}.")

    seen_hours = set()
    for entry in request.hours:
        if not math.isfinite(entry.demand_kwh) or entry.demand_kwh < 0:
            raise InputValidationError(f"Invalid demand_kwh at hour {entry.hour}: {entry.demand_kwh}")
        if not math.isfinite(entry.solar_kwh) or entry.solar_kwh < 0:
            raise InputValidationError(f"Invalid solar_kwh at hour {entry.hour}: {entry.solar_kwh}")
        if not math.isfinite(entry.tariff_bdt_per_kwh) or entry.tariff_bdt_per_kwh < 0:
            raise InputValidationError(f"Invalid tariff_bdt_per_kwh at hour {entry.hour}: {entry.tariff_bdt_per_kwh}")
        
        seen_hours.add(entry.hour)

    if seen_hours != set(range(24)):
        raise InputValidationError("hours must cover exactly 0 through 23 without gaps or duplicates.")

    # Battery check
    bat = request.battery
    if not math.isfinite(bat.capacity_kwh) or bat.capacity_kwh <= 0:
        raise InputValidationError(f"Battery capacity_kwh must be finite and positive, got {bat.capacity_kwh}")
    if not math.isfinite(bat.minimum_energy_kwh) or bat.minimum_energy_kwh < 0 or bat.minimum_energy_kwh > bat.capacity_kwh:
        raise InputValidationError(f"Battery minimum_energy_kwh must be in [0, capacity_kwh], got {bat.minimum_energy_kwh}")
    if not math.isfinite(bat.initial_energy_kwh) or bat.initial_energy_kwh < bat.minimum_energy_kwh or bat.initial_energy_kwh > bat.capacity_kwh:
        raise InputValidationError(f"Battery initial_energy_kwh must be in [minimum_energy_kwh, capacity_kwh], got {bat.initial_energy_kwh}")
    if not math.isfinite(bat.max_charge_kwh_per_hour) or bat.max_charge_kwh_per_hour < 0:
        raise InputValidationError(f"Battery max_charge_kwh_per_hour must be non-negative, got {bat.max_charge_kwh_per_hour}")
    if not math.isfinite(bat.max_discharge_kwh_per_hour) or bat.max_discharge_kwh_per_hour < 0:
        raise InputValidationError(f"Battery max_discharge_kwh_per_hour must be non-negative, got {bat.max_discharge_kwh_per_hour}")
