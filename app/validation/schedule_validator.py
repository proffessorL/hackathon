import math
from typing import List, Dict, Tuple
from app.models.request import OptimizeRequest
from app.models.directives import DirectiveInterpretation, DirectiveType
from app.models.response import HourlyPlanEntry, BatteryAction

class ScheduleValidationError(Exception):
    """Raised when post-optimization replay validation fails."""
    pass

def validate_and_recalculate_schedule(
    request: OptimizeRequest,
    directives: List[DirectiveInterpretation],
    hourly_plan: List[HourlyPlanEntry],
    tol: float = 0.01
) -> Tuple[float, float, float]:
    """
    Deterministically replays and validates the hourly optimization schedule.
    Returns recalculated (total_grid_kwh, total_cost_bdt, peak_grid_kwh).
    Raises ScheduleValidationError if any constraint is violated.
    """
    if len(hourly_plan) != 24:
        raise ScheduleValidationError(f"hourly_plan must contain exactly 24 entries, got {len(hourly_plan)}")

    # Map hours in input
    hour_inputs = {h.hour: h for h in request.hours}
    bat = request.battery

    # Build active directive limits per hour
    effective_solar = {h: hour_inputs[h].solar_kwh for h in range(24)}
    min_reserve = {h: bat.minimum_energy_kwh for h in range(24)}
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid_limit = {h: float('inf') for h in range(24)}

    for directive in directives:
        if not directive.applies or directive.structured_adjustment is None:
            continue
        adj = directive.structured_adjustment
        hours = adj.get("hours", [])

        if directive.directive_type == DirectiveType.SOLAR_REDUCTION:
            factor = adj["factor"]
            for h in hours:
                effective_solar[h] = hour_inputs[h].solar_kwh * factor

        elif directive.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            directive_min = adj["minimum_energy_kwh"]
            for h in hours:
                min_reserve[h] = max(min_reserve[h], directive_min)

        elif directive.directive_type == DirectiveType.NO_CHARGE_WINDOW:
            for h in hours:
                no_charge_hours.add(h)

        elif directive.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
            for h in hours:
                no_discharge_hours.add(h)

        elif directive.directive_type == DirectiveType.MAX_GRID_WINDOW:
            cap = adj["max_grid_kwh"]
            for h in hours:
                max_grid_limit[h] = min(max_grid_limit[h], cap)

    current_battery_energy = bat.initial_energy_kwh
    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0

    for i, plan in enumerate(hourly_plan):
        if plan.hour != i:
            raise ScheduleValidationError(f"Expected hour {i} at plan index {i}, got {plan.hour}")

        # Finite checks
        for val_name, val in [
            ("grid_kwh", plan.grid_kwh),
            ("solar_used_kwh", plan.solar_used_kwh),
            ("battery_kwh", plan.battery_kwh),
            ("battery_energy_after_kwh", plan.battery_energy_after_kwh)
        ]:
            if not math.isfinite(val) or val < -tol:
                raise ScheduleValidationError(f"Hour {plan.hour}: {val_name} must be finite and non-negative, got {val}")

        grid_kwh = max(0.0, plan.grid_kwh)
        solar_used = max(0.0, plan.solar_used_kwh)
        battery_kwh = max(0.0, plan.battery_kwh)
        battery_after = plan.battery_energy_after_kwh
        tariff = hour_inputs[plan.hour].tariff_bdt_per_kwh
        demand = hour_inputs[plan.hour].demand_kwh

        # Solar limit
        if solar_used > effective_solar[plan.hour] + tol:
            raise ScheduleValidationError(
                f"Hour {plan.hour}: solar_used_kwh ({solar_used}) exceeds effective solar ({effective_solar[plan.hour]})"
            )

        # Max grid window
        if grid_kwh > max_grid_limit[plan.hour] + tol:
            raise ScheduleValidationError(
                f"Hour {plan.hour}: grid_kwh ({grid_kwh}) exceeds max grid cap ({max_grid_limit[plan.hour]})"
            )

        # Battery action checks
        charge_kwh = 0.0
        discharge_kwh = 0.0

        if plan.battery_action == BatteryAction.IDLE:
            if abs(battery_kwh) > tol:
                raise ScheduleValidationError(f"Hour {plan.hour}: battery_action is idle but battery_kwh is {battery_kwh}")
            expected_energy_after = current_battery_energy
        elif plan.battery_action == BatteryAction.CHARGE:
            if plan.hour in no_charge_hours:
                raise ScheduleValidationError(f"Hour {plan.hour}: battery charge forbidden by no_charge_window")
            if battery_kwh > bat.max_charge_kwh_per_hour + tol:
                raise ScheduleValidationError(
                    f"Hour {plan.hour}: charge rate ({battery_kwh}) exceeds max charge limit ({bat.max_charge_kwh_per_hour})"
                )
            charge_kwh = battery_kwh
            expected_energy_after = current_battery_energy + charge_kwh
        elif plan.battery_action == BatteryAction.DISCHARGE:
            if plan.hour in no_discharge_hours:
                raise ScheduleValidationError(f"Hour {plan.hour}: battery discharge forbidden by no_discharge_window")
            if battery_kwh > bat.max_discharge_kwh_per_hour + tol:
                raise ScheduleValidationError(
                    f"Hour {plan.hour}: discharge rate ({battery_kwh}) exceeds max discharge limit ({bat.max_discharge_kwh_per_hour})"
                )
            discharge_kwh = battery_kwh
            expected_energy_after = current_battery_energy - discharge_kwh
        else:
            raise ScheduleValidationError(f"Hour {plan.hour}: unknown battery action {plan.battery_action}")

        # Battery transition check
        if abs(battery_after - expected_energy_after) > tol:
            raise ScheduleValidationError(
                f"Hour {plan.hour}: battery_energy_after_kwh ({battery_after}) does not match transition calculation ({expected_energy_after})"
            )

        # Battery capacity & minimum reserve check
        if battery_after > bat.capacity_kwh + tol:
            raise ScheduleValidationError(
                f"Hour {plan.hour}: battery energy ({battery_after}) exceeds capacity ({bat.capacity_kwh})"
            )
        if battery_after < min_reserve[plan.hour] - tol:
            raise ScheduleValidationError(
                f"Hour {plan.hour}: battery energy ({battery_after}) violates minimum reserve ({min_reserve[plan.hour]})"
            )

        # Energy balance check: grid + solar_used + discharge = demand + charge
        supply = grid_kwh + solar_used + discharge_kwh
        demand_total = demand + charge_kwh
        if abs(supply - demand_total) > tol:
            raise ScheduleValidationError(
                f"Hour {plan.hour}: energy balance violation. Supply={supply} != Demand={demand_total}"
            )

        # Update state and recalculations
        current_battery_energy = battery_after
        total_grid_kwh += grid_kwh
        total_cost_bdt += grid_kwh * tariff
        peak_grid_kwh = max(peak_grid_kwh, grid_kwh)

    # End of day neutrality check
    if abs(current_battery_energy - bat.initial_energy_kwh) > tol:
        raise ScheduleValidationError(
            f"End-of-day battery energy ({current_battery_energy}) does not equal initial energy ({bat.initial_energy_kwh})"
        )

    return round(total_grid_kwh, 4), round(total_cost_bdt, 4), round(peak_grid_kwh, 4)
