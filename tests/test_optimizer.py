import json
import pytest
from app.models.request import OptimizeRequest, HourEntry, BatteryConfig
from app.models.directives import (
    DirectiveInterpretation, DirectiveType
)
from app.optimizer.solver import solve_optimization
from app.validation.schedule_validator import validate_and_recalculate_schedule

def get_sample_request() -> OptimizeRequest:
    hours = []
    for h in range(24):
        # Peak hours 17..21 with high tariff
        tariff = 25.0 if 17 <= h <= 21 else 5.0
        # Peak solar 9..15
        solar = 100.0 if 9 <= h <= 15 else 0.0
        demand = 100.0
        hours.append(HourEntry(
            hour=h,
            demand_kwh=demand,
            solar_kwh=solar,
            tariff_bdt_per_kwh=tariff
        ))

    battery = BatteryConfig(
        capacity_kwh=500.0,
        initial_energy_kwh=200.0,
        minimum_energy_kwh=50.0,
        max_charge_kwh_per_hour=100.0,
        max_discharge_kwh_per_hour=100.0
    )

    return OptimizeRequest(
        scenario_id="TEST-001",
        operator_notes=["Normal operation."],
        hours=hours,
        battery=battery
    )

def test_basic_optimization():
    request = get_sample_request()
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation="No operation."
        )
    ]
    hourly_plan, total_grid, total_cost, peak_grid, summary = solve_optimization(request, directives)
    
    assert len(hourly_plan) == 24
    assert total_grid > 0
    assert total_cost > 0
    # End of day battery level must equal initial energy (200.0)
    assert hourly_plan[-1].battery_energy_after_kwh == 200.0

def test_no_charge_window_constraint():
    request = get_sample_request()
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.NO_CHARGE_WINDOW,
            structured_adjustment={"hours": [10, 11, 12]},
            explanation="No charging."
        )
    ]
    hourly_plan, _, _, _, _ = solve_optimization(request, directives)
    
    for entry in hourly_plan:
        if entry.hour in [10, 11, 12]:
            assert entry.battery_action != "charge"

def test_minimum_reserve_constraint():
    request = get_sample_request()
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
            structured_adjustment={"hours": list(range(24)), "minimum_energy_kwh": 150.0},
            explanation="Elevated reserve."
        )
    ]
    hourly_plan, _, _, _, _ = solve_optimization(request, directives)
    
    for entry in hourly_plan:
        assert entry.battery_energy_after_kwh >= 149.99
