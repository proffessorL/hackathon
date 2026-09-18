import logging
import pulp
from typing import List, Tuple
from app.models.request import OptimizeRequest
from app.models.directives import DirectiveInterpretation, DirectiveType
from app.models.response import HourlyPlanEntry, BatteryAction, OptimizeResponse
from app.validation.schedule_validator import validate_and_recalculate_schedule

logger = logging.getLogger(__name__)

class OptimizationError(Exception):
    """Raised when the optimization problem is infeasible or solver fails."""
    pass

def solve_optimization(
    request: OptimizeRequest,
    directives: List[DirectiveInterpretation]
) -> Tuple[List[HourlyPlanEntry], float, float, float, str]:
    """
    Formulates and solves the 24-hour linear program using PuLP.
    Returns (hourly_plan, total_grid_kwh, total_cost_bdt, peak_grid_kwh, plan_summary).
    """
    bat = request.battery
    hour_inputs = {h.hour: h for h in request.hours}

    # Initialize directive bounds
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

    # Build PuLP Linear Program
    prob = pulp.LpProblem("GridWise_Energy_Optimization", pulp.LpMinimize)

    # Decision variables
    g = {}  # Grid import (kWh)
    s = {}  # Solar used (kWh)
    c = {}  # Battery charge (kWh)
    d = {}  # Battery discharge (kWh)
    e = {}  # Battery energy level after hour (kWh)

    for h in range(24):
        # Grid import limit
        g_max = max_grid_limit[h] if max_grid_limit[h] != float('inf') else None
        g[h] = pulp.LpVariable(f"g_{h}", lowBound=0, upBound=g_max, cat="Continuous")
        
        # Solar usage limit
        s[h] = pulp.LpVariable(f"s_{h}", lowBound=0, upBound=effective_solar[h], cat="Continuous")

        # Battery charge limit
        c_max = 0.0 if h in no_charge_hours else bat.max_charge_kwh_per_hour
        c[h] = pulp.LpVariable(f"c_{h}", lowBound=0, upBound=c_max, cat="Continuous")

        # Battery discharge limit
        d_max = 0.0 if h in no_discharge_hours else bat.max_discharge_kwh_per_hour
        d[h] = pulp.LpVariable(f"d_{h}", lowBound=0, upBound=d_max, cat="Continuous")

        # Battery state of charge limit
        e[h] = pulp.LpVariable(f"e_{h}", lowBound=min_reserve[h], upBound=bat.capacity_kwh, cat="Continuous")

    # Objective Function: Minimize total electricity cost BDT
    prob += pulp.lpSum([g[h] * hour_inputs[h].tariff_bdt_per_kwh for h in range(24)])

    # Constraints
    for h in range(24):
        demand = hour_inputs[h].demand_kwh
        # 1. Energy balance: g[h] + s[h] + d[h] == demand + c[h]
        prob += (g[h] + s[h] + d[h] == demand + c[h], f"EnergyBalance_{h}")

        # 2. Battery state transition
        if h == 0:
            prob += (e[0] == bat.initial_energy_kwh + c[0] - d[0], f"BatteryTransition_{h}")
        else:
            prob += (e[h] == e[h-1] + c[h] - d[h], f"BatteryTransition_{h}")

    # End of day neutrality constraint: final energy must equal initial energy
    prob += (e[23] == bat.initial_energy_kwh, "EndOfDayNeutrality")

    # Solve LP problem
    solver = pulp.PULP_CBC_CMD(msg=0)
    status = prob.solve(solver)

    if pulp.LpStatus[status] != "Optimal":
        raise OptimizationError(f"Optimization solver failed to find optimal solution: status={pulp.LpStatus[status]}")

    # Extract results into HourlyPlanEntry list
    hourly_plan: List[HourlyPlanEntry] = []

    for h in range(24):
        g_val = round(max(0.0, float(pulp.value(g[h]))), 4)
        s_val = round(max(0.0, float(pulp.value(s[h]))), 4)
        c_val = round(max(0.0, float(pulp.value(c[h]))), 4)
        d_val = round(max(0.0, float(pulp.value(d[h]))), 4)
        e_val = round(max(0.0, float(pulp.value(e[h]))), 4)

        if c_val > 0.001:
            action = BatteryAction.CHARGE
            b_kwh = c_val
        elif d_val > 0.001:
            action = BatteryAction.DISCHARGE
            b_kwh = d_val
        else:
            action = BatteryAction.IDLE
            b_kwh = 0.0

        hourly_plan.append(HourlyPlanEntry(
            hour=h,
            grid_kwh=g_val,
            solar_used_kwh=s_val,
            battery_action=action,
            battery_kwh=b_kwh,
            battery_energy_after_kwh=e_val
        ))

    # Perform deterministic replay validation and exact recalculation
    total_grid, total_cost, peak_grid = validate_and_recalculate_schedule(
        request, directives, hourly_plan
    )

    # Generate plan summary
    charge_hours = [p.hour for p in hourly_plan if p.battery_action == BatteryAction.CHARGE]
    discharge_hours = [p.hour for p in hourly_plan if p.battery_action == BatteryAction.DISCHARGE]
    
    summary = (
        f"Optimal 24-hour schedule generated for scenario {request.scenario_id}. "
        f"Total grid consumption: {total_grid:.2f} kWh, Total cost: {total_cost:.2f} BDT, Peak grid demand: {peak_grid:.2f} kWh. "
        f"Battery charged during hours {charge_hours if charge_hours else 'None'}, "
        f"and discharged during peak tariff hours {discharge_hours if discharge_hours else 'None'}."
    )

    return hourly_plan, total_grid, total_cost, peak_grid, summary
