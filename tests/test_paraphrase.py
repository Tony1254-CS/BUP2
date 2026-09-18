"""
Live LLM paraphrase tests.

These tests require:
  - GEMINI_API_KEY to be set
  - pytest --run-live flag

They are excluded from the default test suite.
"""
import asyncio
import os

import pytest

from app.llm_interpreter import interpret_notes
from app.schemas import BatteryInput


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="Run live LLM tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live"):
        skip = pytest.mark.skip(reason="need --run-live to run")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)


BAT = BatteryInput(
    capacity_kwh=500, initial_energy_kwh=250,
    minimum_energy_kwh=50, max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)

live = pytest.mark.live


@live
def test_solar_reduction_basic():
    old_skip = os.environ.pop("SKIP_LLM", None)
    old_test = os.environ.pop("GRIDWISE_TEST_MODE", None)
    try:
        results = asyncio.run(interpret_notes(
            ["Please cut solar output by 80% from 10 AM to 2 PM due to panel cleaning."],
            BAT,
        ))
    finally:
        if old_skip: os.environ["SKIP_LLM"] = old_skip
        if old_test: os.environ["GRIDWISE_TEST_MODE"] = old_test
    r = results[0]
    assert r.directive_type == "solar_reduction"
    assert r.applies is True
    adj = r.structured_adjustment
    assert adj is not None
    assert adj["hours"] == [10, 11, 12, 13]
    assert abs(adj["factor"] - 0.2) < 0.01
    assert set(adj.keys()) == {"hours", "factor"}


@live
def test_no_charge_window():
    old_skip = os.environ.pop("SKIP_LLM", None)
    old_test = os.environ.pop("GRIDWISE_TEST_MODE", None)
    try:
        results = asyncio.run(interpret_notes(
            ["No charging the battery during peak evening hours from 6 PM to 10 PM."],
            BAT,
        ))
    finally:
        if old_skip: os.environ["SKIP_LLM"] = old_skip
        if old_test: os.environ["GRIDWISE_TEST_MODE"] = old_test
    r = results[0]
    assert r.directive_type == "no_charge_window"
    assert r.applies is True
    adj = r.structured_adjustment
    assert adj is not None
    assert adj["hours"] == [18, 19, 20, 21]
    assert set(adj.keys()) == {"hours"}


@live
def test_no_op_irrelevant():
    old_skip = os.environ.pop("SKIP_LLM", None)
    old_test = os.environ.pop("GRIDWISE_TEST_MODE", None)
    try:
        results = asyncio.run(interpret_notes(
            ["Had a great lunch today, the cafeteria pasta was excellent."],
            BAT,
        ))
    finally:
        if old_skip: os.environ["SKIP_LLM"] = old_skip
        if old_test: os.environ["GRIDWISE_TEST_MODE"] = old_test
    r = results[0]
    assert r.directive_type == "no_op"
    assert r.applies is False
    assert r.structured_adjustment is None


@live
def test_min_battery_reserve_percentage():
    old_skip = os.environ.pop("SKIP_LLM", None)
    old_test = os.environ.pop("GRIDWISE_TEST_MODE", None)
    try:
        results = asyncio.run(interpret_notes(
            ["Keep at least 40% battery capacity reserved at all times."],
            BAT,
        ))
    finally:
        if old_skip: os.environ["SKIP_LLM"] = old_skip
        if old_test: os.environ["GRIDWISE_TEST_MODE"] = old_test
    r = results[0]
    assert r.directive_type == "minimum_battery_reserve"
    assert r.applies is True
    adj = r.structured_adjustment
    assert adj is not None
    assert abs(adj["minimum_energy_kwh"] - 200.0) < 1.0
    assert set(adj.keys()) == {"hours", "minimum_energy_kwh"}


@live
def test_max_grid_window():
    old_skip = os.environ.pop("SKIP_LLM", None)
    old_test = os.environ.pop("GRIDWISE_TEST_MODE", None)
    try:
        results = asyncio.run(interpret_notes(
            ["From 7 PM to 11 PM, cap the maximum grid usage at 250 kilowatt-hours per hour."],
            BAT,
        ))
    finally:
        if old_skip: os.environ["SKIP_LLM"] = old_skip
        if old_test: os.environ["GRIDWISE_TEST_MODE"] = old_test
    r = results[0]
    assert r.directive_type == "max_grid_window"
    assert r.applies is True
    adj = r.structured_adjustment
    assert adj is not None
    assert adj["hours"] == [19, 20, 21, 22]
    assert abs(adj["max_grid_kwh"] - 250.0) < 1.0
    assert set(adj.keys()) == {"hours", "max_grid_kwh"}
