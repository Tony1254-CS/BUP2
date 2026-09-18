from typing import List, Tuple
from app.schemas import (
    DirectiveInterpretation,
    HourInput,
    BatteryInput,
    OptimizeResponse,
)

def verify_schedule_compliance(
    response: OptimizeResponse,
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
    *,
    check_totals: bool = True,
) -> Tuple[bool, List[str]]:
    """
    Simulates the official organizer judge harness.
    Replays the hourly_plan against all GridWise rules and active directives.
    Returns (is_valid, list_of_violations).
    """
    from app.constants import GRIDWISE_TOL
    violations = []
    

    # 1. Check hours count
    if len(response.hourly_plan) != 24:
        violations.append(f"hourly_plan must contain exactly 24 entries, found {len(response.hourly_plan)}")
        return False, violations

    plan_by_hour = {p.hour: p for p in response.hourly_plan}
    if set(plan_by_hour) != set(range(24)):
        violations.append("hourly_plan contains duplicate or missing hours")
        return False, violations

    hours_by_id = {hour.hour: hour for hour in hours}
    if set(hours_by_id) != set(range(24)):
        violations.append("hours contains duplicate or missing hours")
        return False, violations

    # 2. Recompute effective parameters from directives
    effective_solar = [hours_by_id[h].solar_kwh for h in range(24)]
    min_reserve = [battery.minimum_energy_kwh] * 24
    max_charge = [battery.max_charge_kwh_per_hour] * 24
    max_discharge = [battery.max_discharge_kwh_per_hour] * 24
    max_grid = [float("inf")] * 24

    for d in directives:
        if not d.applies or not d.structured_adjustment:
            continue
        dtype = d.directive_type
        adj = d.structured_adjustment
        aff_hours = adj.get("hours", [])

        if dtype == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            for h in aff_hours:
                if 0 <= h < 24:
                    effective_solar[h] = min(effective_solar[h], hours_by_id[h].solar_kwh * factor)
        elif dtype == "minimum_battery_reserve":
            res_val = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
            for h in aff_hours:
                if 0 <= h < 24:
                    min_reserve[h] = max(min_reserve[h], res_val)
        elif dtype == "no_charge_window":
            for h in aff_hours:
                if 0 <= h < 24:
                    max_charge[h] = 0.0
        elif dtype == "no_discharge_window":
            for h in aff_hours:
                if 0 <= h < 24:
                    max_discharge[h] = 0.0
        elif dtype == "max_grid_window":
            cap_val = float(adj.get("max_grid_kwh", float("inf")))
            for h in aff_hours:
                if 0 <= h < 24:
                    max_grid[h] = min(max_grid[h], cap_val)

    # 3. Step through 24 hours
    current_energy = battery.initial_energy_kwh
    recalc_grid = 0.0
    recalc_cost = 0.0
    recalc_peak = 0.0

    for h in range(24):
        item = plan_by_hour.get(h)
        if not item:
            violations.append(f"Hour {h} missing in hourly_plan")
            continue



        # Non-negative checks
        if item.grid_kwh < -GRIDWISE_TOL or item.solar_used_kwh < -GRIDWISE_TOL or item.battery_kwh < -GRIDWISE_TOL:
            violations.append(f"Hour {h}: negative energy values (grid={item.grid_kwh}, solar={item.solar_used_kwh}, battery={item.battery_kwh})")

        # Solar max
        if item.solar_used_kwh > effective_solar[h] + GRIDWISE_TOL:
            violations.append(f"Hour {h}: solar used {item.solar_used_kwh} exceeds available {effective_solar[h]}")

        # Grid max
        if item.grid_kwh > max_grid[h] + GRIDWISE_TOL:
            violations.append(f"Hour {h}: grid used {item.grid_kwh} exceeds max allowed {max_grid[h]}")

        # Battery bounds based on action
        if item.battery_action == "idle":
            # Just check that it's close to 0
            if item.battery_kwh > GRIDWISE_TOL:
                violations.append(f"Hour {h}: action is idle but battery_kwh is {item.battery_kwh}")
        else:
            charge_amt = item.battery_kwh if item.battery_action == "charge" else 0.0
            discharge_amt = item.battery_kwh if item.battery_action == "discharge" else 0.0
            if charge_amt > max_charge[h] + GRIDWISE_TOL:
                violations.append(f"Hour {h}: charge {charge_amt} exceeds max {max_charge[h]}")
            if discharge_amt > max_discharge[h] + GRIDWISE_TOL:
                violations.append(f"Hour {h}: discharge {discharge_amt} exceeds max {max_discharge[h]}")

        # Energy balance: supply == demand
        supply = item.grid_kwh + item.solar_used_kwh + (item.battery_kwh if item.battery_action == "discharge" else 0.0)
        consumption = hours_by_id[h].demand_kwh + (item.battery_kwh if item.battery_action == "charge" else 0.0)
        if abs(supply - consumption) > GRIDWISE_TOL:
            violations.append(f"Hour {h}: balance mismatch (supply {supply} != consumption {consumption})")

        # Battery tracking (after = before +/- kwh)
        delta = item.battery_kwh if item.battery_action == "charge" else -item.battery_kwh
        expected_after = current_energy + delta
        if abs(item.battery_energy_after_kwh - expected_after) > GRIDWISE_TOL:
            violations.append(f"Hour {h}: battery tracking mismatch (expected {expected_after}, got {item.battery_energy_after_kwh})")

        # Battery absolute limits
        if item.battery_energy_after_kwh < min_reserve[h] - GRIDWISE_TOL:
            violations.append(f"Hour {h}: energy {item.battery_energy_after_kwh} below minimum reserve {min_reserve[h]}")
        if item.battery_energy_after_kwh > battery.capacity_kwh + GRIDWISE_TOL:
            violations.append(f"Hour {h}: energy {item.battery_energy_after_kwh} above capacity {battery.capacity_kwh}")

        # Update for next hour
        current_energy = item.battery_energy_after_kwh
        recalc_grid += item.grid_kwh
        recalc_cost += item.grid_kwh * hours_by_id[h].tariff_bdt_per_kwh
        if item.grid_kwh > recalc_peak:
            recalc_peak = item.grid_kwh

    # 4. End-of-day battery neutrality
    if abs(current_energy - battery.initial_energy_kwh) > GRIDWISE_TOL:
        violations.append(f"End-of-day neutrality broken: final={current_energy:.2f} != initial={battery.initial_energy_kwh:.2f}")

    # Totals are checked when verifying a completed response. The production
    # endpoint disables this for its provisional response so totals are
    # computed only after the schedule itself passes replay.
    if check_totals:
        if abs(response.total_grid_kwh - recalc_grid) > GRIDWISE_TOL:
            violations.append(f"total_grid_kwh mismatch: reported={response.total_grid_kwh:.2f}, recalculated={recalc_grid:.2f}")
        if abs(response.total_cost_bdt - recalc_cost) > GRIDWISE_TOL:
            violations.append(f"total_cost_bdt mismatch: reported={response.total_cost_bdt:.2f}, recalculated={recalc_cost:.2f}")
        if abs(response.peak_grid_kwh - recalc_peak) > GRIDWISE_TOL:
            violations.append(f"peak_grid_kwh mismatch: reported={response.peak_grid_kwh:.2f}, recalculated={recalc_peak:.2f}")

    is_valid = (len(violations) == 0)
    return is_valid, violations
