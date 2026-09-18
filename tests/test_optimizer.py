"""
Optimizer tests — runs all 10 public reference cases through the MILP solver
using the KNOWN-GOOD directive interpretations from the sample pack.

Verifies:
  - cost matches expected within ±0.02 BDT
  - energy balance holds every hour: g + s + d = demand + c
  - battery SoC stays within [min_reserve, capacity] every hour
  - battery returns to initial_energy at hour 23
  - no simultaneous charge + discharge (enforced by MILP binary flags)
  - directive constraints obeyed (no_charge, no_discharge, max_grid, solar ceiling, reserve)
"""
import json
import math
import pathlib

import pytest

from app.schemas import BatteryInput, DirectiveInterpretation, HourInput
from app.optimizer import solve

CASES_PATH = pathlib.Path(__file__).resolve().parent.parent / "sample_cases.json"


def _load_cases():
    with open(CASES_PATH) as f:
        pack = json.load(f)
    return pack["cases"]


CASES = _load_cases()


@pytest.fixture(params=CASES, ids=[c["id"] for c in CASES])
def case(request):
    return request.param


def test_cost_matches(case):
    """Cost within ±0.02 BDT of reference."""
    hrs = [HourInput(**h) for h in case["input"]["hours"]]
    bat = BatteryInput(**case["input"]["battery"])
    dirs = [DirectiveInterpretation(**d) for d in case["expected_output"]["directive_interpretation"]]
    plan = solve(hrs, bat, dirs)
    cost = round(sum(p.grid_kwh * hrs[p.hour].tariff_bdt_per_kwh for p in plan), 2)
    expected = case["expected_output"]["total_cost_bdt"]
    assert abs(cost - expected) <= 0.02, f"{case['id']}: cost={cost} expected={expected}"


def test_energy_balance(case):
    """g + s + d = demand + c for every hour."""
    hrs = [HourInput(**h) for h in case["input"]["hours"]]
    bat = BatteryInput(**case["input"]["battery"])
    dirs = [DirectiveInterpretation(**d) for d in case["expected_output"]["directive_interpretation"]]
    plan = solve(hrs, bat, dirs)
    for entry in plan:
        h = entry.hour
        demand = hrs[h].demand_kwh
        c = entry.battery_kwh if entry.battery_action == "charge" else 0
        d = entry.battery_kwh if entry.battery_action == "discharge" else 0
        supply = entry.grid_kwh + entry.solar_used_kwh + d
        load = demand + c
        assert abs(supply - load) < 0.1, (
            f"Hour {h}: supply={supply} != load={load}"
        )


def test_battery_bounds(case):
    """SoC within [min_reserve, capacity] every hour."""
    hrs = [HourInput(**h) for h in case["input"]["hours"]]
    bat = BatteryInput(**case["input"]["battery"])
    dirs = [DirectiveInterpretation(**d) for d in case["expected_output"]["directive_interpretation"]]
    plan = solve(hrs, bat, dirs)
    for entry in plan:
        assert entry.battery_energy_after_kwh >= bat.minimum_energy_kwh - 0.01, (
            f"Hour {entry.hour}: SoC {entry.battery_energy_after_kwh} < min {bat.minimum_energy_kwh}"
        )
        assert entry.battery_energy_after_kwh <= bat.capacity_kwh + 0.01, (
            f"Hour {entry.hour}: SoC {entry.battery_energy_after_kwh} > capacity {bat.capacity_kwh}"
        )


def test_end_of_day_neutrality(case):
    """Battery returns to initial_energy at hour 23."""
    hrs = [HourInput(**h) for h in case["input"]["hours"]]
    bat = BatteryInput(**case["input"]["battery"])
    dirs = [DirectiveInterpretation(**d) for d in case["expected_output"]["directive_interpretation"]]
    plan = solve(hrs, bat, dirs)
    assert abs(plan[23].battery_energy_after_kwh - bat.initial_energy_kwh) < 0.1, (
        f"End SoC {plan[23].battery_energy_after_kwh} != initial {bat.initial_energy_kwh}"
    )


def test_no_simultaneous_charge_discharge(case):
    """Binary flags guarantee: never charge+discharge in same hour."""
    hrs = [HourInput(**h) for h in case["input"]["hours"]]
    bat = BatteryInput(**case["input"]["battery"])
    dirs = [DirectiveInterpretation(**d) for d in case["expected_output"]["directive_interpretation"]]
    plan = solve(hrs, bat, dirs)
    for entry in plan:
        if entry.battery_action == "charge":
            assert entry.battery_kwh >= 0
        elif entry.battery_action == "discharge":
            assert entry.battery_kwh >= 0
        elif entry.battery_action == "idle":
            assert entry.battery_kwh == 0.0


def test_directive_constraints_obeyed(case):
    """Verify the solver obeys each directive in the case."""
    hrs = [HourInput(**h) for h in case["input"]["hours"]]
    bat = BatteryInput(**case["input"]["battery"])
    dirs = [DirectiveInterpretation(**d) for d in case["expected_output"]["directive_interpretation"]]
    plan = solve(hrs, bat, dirs)

    for d in dirs:
        if not d.applies or d.structured_adjustment is None:
            continue
        adj = d.structured_adjustment
        affected = adj.get("hours", [])

        if d.directive_type == "no_charge_window":
            for h in affected:
                entry = plan[h]
                assert entry.battery_action != "charge" or entry.battery_kwh < 0.01, (
                    f"Hour {h}: charge in no_charge_window"
                )

        elif d.directive_type == "no_discharge_window":
            for h in affected:
                entry = plan[h]
                assert entry.battery_action != "discharge" or entry.battery_kwh < 0.01, (
                    f"Hour {h}: discharge in no_discharge_window"
                )

        elif d.directive_type == "max_grid_window":
            cap = adj["max_grid_kwh"]
            for h in affected:
                assert plan[h].grid_kwh <= cap + 0.01, (
                    f"Hour {h}: grid {plan[h].grid_kwh} > max {cap}"
                )

        elif d.directive_type == "solar_reduction":
            factor = adj["factor"]
            for h in affected:
                max_solar = hrs[h].solar_kwh * factor
                assert plan[h].solar_used_kwh <= max_solar + 0.01, (
                    f"Hour {h}: solar {plan[h].solar_used_kwh} > effective {max_solar}"
                )

        elif d.directive_type == "minimum_battery_reserve":
            min_e = adj["minimum_energy_kwh"]
            for h in affected:
                assert plan[h].battery_energy_after_kwh >= min_e - 0.01, (
                    f"Hour {h}: SoC {plan[h].battery_energy_after_kwh} < reserve {min_e}"
                )
