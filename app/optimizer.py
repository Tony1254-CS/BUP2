"""
24-hour campus energy MILP optimizer.

Formulation
-----------
Decision variables per hour h (0..23), 7 per hour = 168 total:
  g[h]  – grid import          (continuous ≥ 0)
  s[h]  – solar used           (continuous ≥ 0)
  c[h]  – battery charge       (continuous ≥ 0)
  d[h]  – battery discharge    (continuous ≥ 0)
  E[h]  – battery SoC after h  (continuous)
  ic[h] – is-charging flag     (binary {0,1})
  id[h] – is-discharging flag  (binary {0,1})

Objective:  minimise  Σ tariff[h] · g[h]

Hard constraints (all from spec):
  1  Energy balance        g + s + d − c = demand       ∀ h
  2  Battery transition    E[h] = E[h-1] + c − d        ∀ h  (E[-1] = initial)
  3  End-of-day neutrality E[23] = initial_energy
  4  Charge linking        c[h] ≤ max_charge[h] · ic[h] ∀ h
  5  Discharge linking     d[h] ≤ max_discharge[h]·id[h] ∀ h
  6  Mutual exclusion      ic[h] + id[h] ≤ 1            ∀ h

Variable bounds (spec + directive effects):
  g ∈ [0, max_grid[h]]
  s ∈ [0, effective_solar[h]]
  c ∈ [0, max_charge[h]]
  d ∈ [0, max_discharge[h]]
  E ∈ [min_reserve[h], capacity]
  ic, id ∈ {0, 1}

The solver is SciPy's HiGHS MILP (scipy.optimize.milp).
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds

from app.schemas import (
    BatteryInput,
    DirectiveInterpretation,
    HourInput,
    HourlyPlanEntry,
)

N = 24
VARS_PER_HOUR = 7
N_VAR = N * VARS_PER_HOUR

# Indices within each hour-block
G, S, C, D, E, IC, ID = range(VARS_PER_HOUR)


def _vi(h: int, v: int) -> int:
    """Flat index for variable *v* at hour *h*."""
    return h * VARS_PER_HOUR + v


def _apply_directives(
    hours: list[HourInput],
    battery: BatteryInput,
    directives: list[DirectiveInterpretation],
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """
    Return per-hour effective parameters after applying directives.
    Returns (eff_solar, min_reserve, max_charge, max_discharge, max_grid).
    """
    hours_by_id = {hour.hour: hour for hour in hours}
    if set(hours_by_id) != set(range(N)):
        raise ValueError("hours must contain each hour from 0 through 23 exactly once")
    eff_solar = [hours_by_id[h].solar_kwh for h in range(N)]
    min_reserve = [battery.minimum_energy_kwh] * N
    max_charge = [battery.max_charge_kwh_per_hour] * N
    max_discharge = [battery.max_discharge_kwh_per_hour] * N
    max_grid = [1e9] * N  # effectively uncapped unless directive says otherwise

    for d in directives:
        if not d.applies or d.structured_adjustment is None:
            continue
        adj = d.structured_adjustment
        affected = adj.get("hours", [])

        if d.directive_type == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            for h in affected:
                if 0 <= h < N:
                    eff_solar[h] = min(eff_solar[h], hours_by_id[h].solar_kwh * factor)

        elif d.directive_type == "minimum_battery_reserve":
            val = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
            for h in affected:
                if 0 <= h < N:
                    min_reserve[h] = max(min_reserve[h], val)

        elif d.directive_type == "no_charge_window":
            for h in affected:
                if 0 <= h < N:
                    max_charge[h] = 0.0

        elif d.directive_type == "no_discharge_window":
            for h in affected:
                if 0 <= h < N:
                    max_discharge[h] = 0.0

        elif d.directive_type == "max_grid_window":
            cap = float(adj.get("max_grid_kwh", 1e9))
            for h in affected:
                if 0 <= h < N:
                    max_grid[h] = min(max_grid[h], cap)

    return eff_solar, min_reserve, max_charge, max_discharge, max_grid


def solve(
    hours: list[HourInput],
    battery: BatteryInput,
    directives: list[DirectiveInterpretation],
) -> list[HourlyPlanEntry]:
    """
    Solve the 24-hour MILP and return the hourly plan.

    Raises RuntimeError if HiGHS reports infeasible / unbounded.
    """
    eff_solar, min_res, mx_charge, mx_discharge, mx_grid = _apply_directives(
        hours, battery, directives
    )

    capacity = battery.capacity_kwh
    init_e = battery.initial_energy_kwh
    hours_by_id = {hour.hour: hour for hour in hours}
    if set(hours_by_id) != set(range(N)):
        raise ValueError("hours must contain each hour from 0 through 23 exactly once")
    demand = [hours_by_id[h].demand_kwh for h in range(N)]
    tariff = [hours_by_id[h].tariff_bdt_per_kwh for h in range(N)]

    # ── Objective ─────────────────────────────────────────────────────────
    c_obj = np.zeros(N_VAR)
    for h in range(N):
        c_obj[_vi(h, G)] = tariff[h]
        # Tiny penalty on binary flags so solver prefers idle when cost-free
        c_obj[_vi(h, IC)] = 1e-7
        c_obj[_vi(h, ID)] = 1e-7

    # ── Bounds ────────────────────────────────────────────────────────────
    lb = np.zeros(N_VAR)
    ub = np.full(N_VAR, np.inf)
    integrality = np.zeros(N_VAR, dtype=int)

    for h in range(N):
        ub[_vi(h, G)] = mx_grid[h]
        ub[_vi(h, S)] = eff_solar[h]
        ub[_vi(h, C)] = mx_charge[h]
        ub[_vi(h, D)] = mx_discharge[h]
        lb[_vi(h, E)] = min_res[h]
        ub[_vi(h, E)] = capacity
        # Binary flags
        ub[_vi(h, IC)] = 1.0
        ub[_vi(h, ID)] = 1.0
        integrality[_vi(h, IC)] = 1
        integrality[_vi(h, ID)] = 1

    # ── Constraints ───────────────────────────────────────────────────────
    # Rows:  24 demand-balance + 24 battery-transition + 1 end-of-day
    #      + 24 charge-link + 24 discharge-link + 24 mutual-exclusion
    n_rows = 24 + 24 + 1 + 24 + 24 + 24  # = 121
    A = np.zeros((n_rows, N_VAR))
    con_lb = np.full(n_rows, -np.inf)
    con_ub = np.full(n_rows, np.inf)
    row = 0

    # 1. Demand balance:  g + s + d − c = demand
    for h in range(N):
        A[row, _vi(h, G)] = 1.0
        A[row, _vi(h, S)] = 1.0
        A[row, _vi(h, D)] = 1.0
        A[row, _vi(h, C)] = -1.0
        con_lb[row] = demand[h]
        con_ub[row] = demand[h]
        row += 1

    # 2. Battery transition:  E[h] − c[h] + d[h] (− E[h-1]) = rhs
    for h in range(N):
        A[row, _vi(h, E)] = 1.0
        A[row, _vi(h, C)] = -1.0
        A[row, _vi(h, D)] = 1.0
        if h > 0:
            A[row, _vi(h - 1, E)] = -1.0
        rhs = init_e if h == 0 else 0.0
        con_lb[row] = rhs
        con_ub[row] = rhs
        row += 1

    # 3. End-of-day neutrality:  E[23] = initial_energy
    A[row, _vi(23, E)] = 1.0
    con_lb[row] = init_e
    con_ub[row] = init_e
    row += 1

    # 4. Charge linking:  c[h] − max_charge[h] · ic[h] ≤ 0
    for h in range(N):
        A[row, _vi(h, C)] = 1.0
        A[row, _vi(h, IC)] = -mx_charge[h]
        con_ub[row] = 0.0
        row += 1

    # 5. Discharge linking:  d[h] − max_discharge[h] · id[h] ≤ 0
    for h in range(N):
        A[row, _vi(h, D)] = 1.0
        A[row, _vi(h, ID)] = -mx_discharge[h]
        con_ub[row] = 0.0
        row += 1

    # 6. Mutual exclusion:  ic[h] + id[h] ≤ 1
    for h in range(N):
        A[row, _vi(h, IC)] = 1.0
        A[row, _vi(h, ID)] = 1.0
        con_ub[row] = 1.0
        row += 1

    assert row == n_rows

    # ── Solve ─────────────────────────────────────────────────────────────
    constraints = LinearConstraint(A, con_lb, con_ub)
    result = milp(
        c=c_obj,
        constraints=constraints,
        integrality=integrality,
        bounds=Bounds(lb, ub),
        options={"time_limit": 10},
    )

    if not result.success:
        raise RuntimeError(f"MILP solver failed: {result.message}")

    x = result.x

    # ── Extract hourly plan ───────────────────────────────────────────────
    plan: list[HourlyPlanEntry] = []
    for h in range(N):
        g_val = round(float(x[_vi(h, G)]), 6)
        s_val = round(float(x[_vi(h, S)]), 6)
        c_val = round(float(x[_vi(h, C)]), 6)
        d_val = round(float(x[_vi(h, D)]), 6)
        e_val = round(float(x[_vi(h, E)]), 6)

        # Clean near-zero noise
        if g_val < 1e-6:
            g_val = 0.0
        if s_val < 1e-6:
            s_val = 0.0
        if c_val < 1e-6:
            c_val = 0.0
        if d_val < 1e-6:
            d_val = 0.0

        # Binary flags determine action directly
        is_charging = int(round(x[_vi(h, IC)]))
        is_discharging = int(round(x[_vi(h, ID)]))

        if is_charging and c_val > 0:
            action = "charge"
            batt_kwh = c_val
        elif is_discharging and d_val > 0:
            action = "discharge"
            batt_kwh = d_val
        else:
            action = "idle"
            batt_kwh = 0.0

        plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=g_val,
                solar_used_kwh=s_val,
                battery_action=action,  # type: ignore[arg-type]
                battery_kwh=batt_kwh,
                battery_energy_after_kwh=e_val,
            )
        )

    return plan
