import asyncio

import pytest
from app.llm_interpreter import interpret_notes
from app.schemas import BatteryInput
from app.constants import GRIDWISE_TOL

BAT = BatteryInput(
    capacity_kwh=500, initial_energy_kwh=250,
    minimum_energy_kwh=50, max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)

live = pytest.mark.live

# Robust Paraphrase Benchmark
SOLAR_CASES = [
    ("Cut solar output by 80% from 10 AM to 2 PM.", [10,11,12,13], 0.2),
    ("Reduce solar by 80 percent between 10:00 and 14:00.", [10,11,12,13], 0.2),
    ("Solar falls to one-fifth from 10 AM to 2 PM.", [10,11,12,13], 0.2),
    ("Leave 20% of normal solar starting at 10:00 until 14:00.", [10,11,12,13], 0.2),
]
NO_CHARGE_CASES = [
    ("No charging the battery during peak evening hours from 6 PM to 10 PM.", [18,19,20,21]),
    ("Do not charge from 18:00 to 22:00.", [18,19,20,21]),
    ("Battery charging is disabled between six PM and ten PM.", [18,19,20,21]),
]
RESERVE_CASES = [
    ("Keep at least 40% battery capacity reserved at all times.", 200.0),
    ("Maintain at least 200 kWh.", 200.0),
    ("Battery should never fall below 200.", 200.0),
]
NO_OP_CASES = [
    "Had a great lunch today, the cafeteria pasta was excellent.",
    "Weather looks cloudy tomorrow.",
    "Please tell me a joke about electricity.",
]

@live
@pytest.mark.parametrize("note, expected_hours, expected_factor", SOLAR_CASES)
def test_solar_robustness(note, expected_hours, expected_factor):
    results = asyncio.run(interpret_notes([note], BAT))
    r = results[0]
    assert r.directive_type == "solar_reduction"
    assert r.structured_adjustment["hours"] == expected_hours
    assert abs(r.structured_adjustment["factor"] - expected_factor) <= GRIDWISE_TOL

@live
@pytest.mark.parametrize("note, expected_hours", NO_CHARGE_CASES)
def test_no_charge_robustness(note, expected_hours):
    results = asyncio.run(interpret_notes([note], BAT))
    r = results[0]
    assert r.directive_type == "no_charge_window"
    assert r.structured_adjustment["hours"] == expected_hours

@live
@pytest.mark.parametrize("note, expected_kwh", RESERVE_CASES)
def test_reserve_robustness(note, expected_kwh):
    results = asyncio.run(interpret_notes([note], BAT))
    r = results[0]
    assert r.directive_type == "minimum_battery_reserve"
    assert abs(r.structured_adjustment["minimum_energy_kwh"] - expected_kwh) <= GRIDWISE_TOL

@live
@pytest.mark.parametrize("note", NO_OP_CASES)
def test_no_op_robustness(note):
    results = asyncio.run(interpret_notes([note], BAT))
    assert results[0].directive_type == "no_op"
