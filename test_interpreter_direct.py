import asyncio

from app.llm_interpreter import interpret_notes
from app.schemas import BatteryInput


def test_current_interpreter_returns_one_entry_per_note(monkeypatch):
    monkeypatch.setenv("SKIP_LLM", "true")
    monkeypatch.setenv("GRIDWISE_TEST_MODE", "true")
    battery = BatteryInput(
        capacity_kwh=500,
        initial_energy_kwh=250,
        minimum_energy_kwh=50,
        max_charge_kwh_per_hour=100,
        max_discharge_kwh_per_hour=100,
    )
    result = asyncio.run(interpret_notes(["first", "second"], battery))
    assert [item.note_index for item in result] == [0, 1]
    assert all(item.directive_type == "no_op" for item in result)
