"""
Paraphrase tests — runs real Gemini calls against paraphrased operator notes
to verify the interpreter handles natural-language variation.

Reads test cases from ``tests/paraphrase_cases.json``.

To run:  pytest tests/test_paraphrase.py -v -s
Requires GEMINI_API_KEY to be set.

The paraphrase_cases.json schema is:
[
  {
    "note": "the paraphrased operator note text",
    "expected_type": "solar_reduction | minimum_battery_reserve | ...",
    "expected_applies": true | false,
    "battery": { ...optional override, uses default if omitted... }
  },
  ...
]
"""
import asyncio
import json
import os
import pathlib

import pytest

from app.llm_interpreter import interpret_notes
from app.schemas import BatteryInput

CASES_PATH = pathlib.Path(__file__).resolve().parent / "paraphrase_cases.json"

DEFAULT_BATTERY = BatteryInput(
    capacity_kwh=500,
    initial_energy_kwh=250,
    minimum_energy_kwh=50,
    max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)

skip_no_key = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set",
)
skip_no_cases = pytest.mark.skipif(
    not CASES_PATH.exists(),
    reason="paraphrase_cases.json not found",
)


def _load_paraphrase_cases():
    if not CASES_PATH.exists():
        return []
    with open(CASES_PATH) as f:
        return json.load(f)


PARA_CASES = _load_paraphrase_cases()


@skip_no_key
@skip_no_cases
@pytest.mark.parametrize(
    "pcase",
    PARA_CASES,
    ids=[f"para-{i}" for i in range(len(PARA_CASES))],
)
def test_paraphrase_interpretation(pcase):
    """Each paraphrased note should map to the expected directive_type."""
    note = pcase["note"]
    expected_type = pcase["expected_type"]
    expected_applies = pcase.get("expected_applies", expected_type != "no_op")

    bat_data = pcase.get("battery")
    battery = BatteryInput(**bat_data) if bat_data else DEFAULT_BATTERY

    # Unset SKIP_LLM if it was set by another test
    old = os.environ.pop("SKIP_LLM", None)
    old_test_mode = os.environ.pop("GRIDWISE_TEST_MODE", None)
    try:
        results = asyncio.run(interpret_notes([note], battery))
    finally:
        if old is not None:
            os.environ["SKIP_LLM"] = old
        if old_test_mode is not None:
            os.environ["GRIDWISE_TEST_MODE"] = old_test_mode

    assert len(results) == 1
    r = results[0]
    assert r.directive_type == expected_type, (
        f"Expected {expected_type}, got {r.directive_type}. "
        f"Explanation: {r.explanation}"
    )
    assert r.applies == expected_applies, (
        f"Expected applies={expected_applies}, got {r.applies}"
    )
