"""
End-to-end API tests using the FastAPI test client.

Uses SKIP_LLM=true so no Gemini calls are made — tests the full
API contract (schema validation, optimizer, totals computation)
without LLM dependency.
"""
import os


from fastapi.testclient import TestClient

# Force LLM skip for e2e tests
os.environ["SKIP_LLM"] = "true"
os.environ["GRIDWISE_TEST_MODE"] = "true"

from app.main import app
import app.main as main_module
from app import verifier
from app.constants import GRIDWISE_TOL

client = TestClient(app, raise_server_exceptions=False)




def _make_payload(scenario_id="TEST-1", notes=None, demand=100.0, solar=50.0, tariff=5.0):
    """Build a valid request payload with synthetic data."""
    if notes is None:
        notes = ["No special instructions today."]
    return {
        "scenario_id": scenario_id,
        "operator_notes": notes,
        "hours": [
            {"hour": h, "demand_kwh": demand, "solar_kwh": solar,
             "tariff_bdt_per_kwh": tariff}
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 500,
            "initial_energy_kwh": 250,
            "minimum_energy_kwh": 50,
            "max_charge_kwh_per_hour": 100,
            "max_discharge_kwh_per_hour": 100,
        },
    }


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_schema_validation_empty_notes():
    payload = _make_payload()
    payload["operator_notes"] = []
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_schema_validation_missing_field():
    resp = client.post("/optimize-energy", json={
        "operator_notes": ["test"],
        "hours": [],
        "battery": {
            "capacity_kwh": 500, "initial_energy_kwh": 250,
            "minimum_energy_kwh": 50, "max_charge_kwh_per_hour": 100,
            "max_discharge_kwh_per_hour": 100,
        },
    })
    assert resp.status_code == 400


def test_schema_validation_rejects_coerced_hour_type():
    payload = _make_payload()
    payload["hours"][0]["hour"] = "0"  # string instead of int
    assert client.post("/optimize-energy", json=payload).status_code == 400


def test_response_structure():
    resp = client.post("/optimize-energy", json=_make_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert "scenario_id" in body
    assert "directive_interpretation" in body
    assert "hourly_plan" in body
    assert "total_grid_kwh" in body
    assert "total_cost_bdt" in body
    assert "peak_grid_kwh" in body
    assert "plan_summary" in body
    assert len(body["hourly_plan"]) == 24
    # All directives should be no_op since LLM is skipped
    for d in body["directive_interpretation"]:
        assert d["directive_type"] == "no_op"
        assert d["applies"] is False


def test_totals_consistent():
    payload = _make_payload()
    resp = client.post("/optimize-energy", json=payload)
    body = resp.json()
    plan = body["hourly_plan"]
    hours = payload["hours"]
    total_grid = round(sum(p["grid_kwh"] for p in plan), 2)
    total_cost = round(
        sum(p["grid_kwh"] * hours[p["hour"]]["tariff_bdt_per_kwh"] for p in plan), 2
    )
    peak = max(p["grid_kwh"] for p in plan)
    assert abs(body["total_grid_kwh"] - total_grid) <= GRIDWISE_TOL
    assert abs(body["total_cost_bdt"] - total_cost) <= GRIDWISE_TOL
    assert abs(body["peak_grid_kwh"] - peak) <= GRIDWISE_TOL


def test_verifier_is_called(monkeypatch):
    calls = []
    original = verifier.verify_schedule_compliance

    def recording(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(verifier, "verify_schedule_compliance", recording)
    resp = client.post("/optimize-energy", json=_make_payload())
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0][1]["check_totals"] is False


def test_rejects_failed_verification(monkeypatch):
    monkeypatch.setattr(
        verifier, "verify_schedule_compliance",
        lambda *a, **kw: (False, ["synthetic failure"]),
    )
    resp = client.post("/optimize-energy", json=_make_payload())
    assert resp.status_code == 500


def test_rejects_failed_llm(monkeypatch):
    async def fail(*a, **kw):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(main_module, "interpret_notes", fail)
    resp = client.post("/optimize-energy", json=_make_payload())
    assert resp.status_code == 500


def test_multiple_notes():
    payload = _make_payload(notes=["note1", "note2", "note3"])
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["directive_interpretation"]) == 3
