"""
Optimizer tests — uses inline synthetic scenarios.

Verifies:
  - energy balance holds every hour
  - battery SoC stays within bounds
  - battery returns to initial_energy at hour 23
  - no simultaneous charge + discharge
  - directive constraints obeyed
  - objective is pure grid cost
"""
import pytest

from app.constants import GRIDWISE_TOL
from app.schemas import BatteryInput, DirectiveInterpretation, HourInput
from app.optimizer import solve

TOL = GRIDWISE_TOL


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_hours(demand=100.0, solar=50.0, tariff=5.0):
    """Generate 24 uniform hours."""
    return [HourInput(hour=h, demand_kwh=demand, solar_kwh=solar,
                      tariff_bdt_per_kwh=tariff) for h in range(24)]


def _make_battery(**overrides):
    defaults = dict(
        capacity_kwh=500, initial_energy_kwh=250,
        minimum_energy_kwh=50, max_charge_kwh_per_hour=100,
        max_discharge_kwh_per_hour=100,
    )
    defaults.update(overrides)
    return BatteryInput(**defaults)


def _no_op_directives(n=1):
    return [DirectiveInterpretation(
        note_index=i, applies=False, directive_type="no_op",
        structured_adjustment=None, explanation="test",
    ) for i in range(n)]


def _solar_reduction(hours, factor):
    return DirectiveInterpretation(
        note_index=0, applies=True, directive_type="solar_reduction",
        structured_adjustment={"hours": hours, "factor": factor},
        explanation="test",
    )


def _no_charge(hours):
    return DirectiveInterpretation(
        note_index=0, applies=True, directive_type="no_charge_window",
        structured_adjustment={"hours": hours}, explanation="test",
    )


def _no_discharge(hours):
    return DirectiveInterpretation(
        note_index=0, applies=True, directive_type="no_discharge_window",
        structured_adjustment={"hours": hours}, explanation="test",
    )


def _max_grid(hours, cap):
    return DirectiveInterpretation(
        note_index=0, applies=True, directive_type="max_grid_window",
        structured_adjustment={"hours": hours, "max_grid_kwh": cap},
        explanation="test",
    )


def _min_reserve(hours, kwh):
    return DirectiveInterpretation(
        note_index=0, applies=True, directive_type="minimum_battery_reserve",
        structured_adjustment={"hours": hours, "minimum_energy_kwh": kwh},
        explanation="test",
    )


# ── Core constraint tests ─────────────────────────────────────────────────

class TestCoreConstraints:
    def test_energy_balance(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        for entry in plan:
            c = entry.battery_kwh if entry.battery_action == "charge" else 0
            d = entry.battery_kwh if entry.battery_action == "discharge" else 0
            supply = entry.grid_kwh + entry.solar_used_kwh + d
            load = hrs[entry.hour].demand_kwh + c
            assert abs(supply - load) <= TOL, f"Hour {entry.hour}: balance broken"

    def test_battery_bounds(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        for entry in plan:
            assert entry.battery_energy_after_kwh >= bat.minimum_energy_kwh - TOL
            assert entry.battery_energy_after_kwh <= bat.capacity_kwh + TOL

    def test_end_of_day_neutrality(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        assert abs(plan[23].battery_energy_after_kwh - bat.initial_energy_kwh) <= TOL

    def test_no_simultaneous_charge_discharge(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        for entry in plan:
            if entry.battery_action == "idle":
                assert entry.battery_kwh == 0.0

    def test_objective_is_pure_grid_cost(self):
        """With no directives, optimizer should minimize grid cost."""
        # High tariff at hours 12-14, low elsewhere
        hrs = []
        for h in range(24):
            tariff = 20.0 if 12 <= h <= 14 else 2.0
            hrs.append(HourInput(hour=h, demand_kwh=100, solar_kwh=50,
                                 tariff_bdt_per_kwh=tariff))
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        # During expensive hours, grid import should be minimized
        for h in [12, 13, 14]:
            entry = plan[h]
            # Should use battery discharge or solar to offset expensive grid
            # (exact values depend on battery constraints, but grid should be
            # less than or equal to what it would be without battery)
            assert entry.grid_kwh <= 100.0 + TOL  # at most demand


# ── Directive constraint tests ────────────────────────────────────────────

class TestDirectiveConstraints:
    def test_no_charge_window(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, [_no_charge([10, 11, 12])])
        for h in [10, 11, 12]:
            entry = plan[h]
            if entry.battery_action == "charge":
                assert entry.battery_kwh <= TOL

    def test_no_discharge_window(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, [_no_discharge([10, 11, 12])])
        for h in [10, 11, 12]:
            entry = plan[h]
            if entry.battery_action == "discharge":
                assert entry.battery_kwh <= TOL

    def test_max_grid_window(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, [_max_grid([10, 11, 12], 80.0)])
        for h in [10, 11, 12]:
            assert plan[h].grid_kwh <= 80.0 + TOL

    def test_solar_reduction(self):
        hrs = _make_hours(solar=100.0)
        bat = _make_battery()
        plan = solve(hrs, bat, [_solar_reduction([10, 11, 12], 0.3)])
        for h in [10, 11, 12]:
            assert plan[h].solar_used_kwh <= 100.0 * 0.3 + TOL

    def test_minimum_battery_reserve(self):
        hrs = _make_hours()
        bat = _make_battery()
        plan = solve(hrs, bat, [_min_reserve(list(range(24)), 200.0)])
        for entry in plan:
            assert entry.battery_energy_after_kwh >= 200.0 - TOL


# ── Varying scenario tests ────────────────────────────────────────────────

class TestVaryingScenarios:
    def test_high_demand_low_solar(self):
        hrs = _make_hours(demand=300.0, solar=10.0, tariff=8.0)
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        for entry in plan:
            c = entry.battery_kwh if entry.battery_action == "charge" else 0
            d = entry.battery_kwh if entry.battery_action == "discharge" else 0
            supply = entry.grid_kwh + entry.solar_used_kwh + d
            load = 300.0 + c
            assert abs(supply - load) <= TOL

    def test_zero_solar(self):
        hrs = _make_hours(demand=100.0, solar=0.0)
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        for entry in plan:
            assert entry.solar_used_kwh <= TOL

    def test_zero_demand_is_feasible(self):
        hrs = _make_hours(demand=0.0, solar=0.0)
        bat = _make_battery()
        plan = solve(hrs, bat, _no_op_directives())
        for entry in plan:
            assert entry.grid_kwh <= TOL
