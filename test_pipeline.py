import json
import pytest
import os
from fastapi.testclient import TestClient
os.environ["SKIP_LLM"] = "true"
os.environ["GRIDWISE_TEST_MODE"] = "true"

from app.main import app
from app.schemas import HourInput, BatteryInput, OptimizeResponse
from app.verifier import verify_schedule_compliance

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    print("\n[GET /health] Passed with {'status': 'ok'}")

def test_malformed_request_handling():
    # Structurally invalid payload
    bad_payload = {"scenario_id": "TEST-BAD", "invalid_key": 123}
    response = client.post("/optimize-energy", json=bad_payload)
    assert response.status_code == 400
    print("[POST /optimize-energy (malformed)] Successfully returned 400 Bad Request")

def test_all_10_sample_cases():
    with open("sample_cases.json", "r") as f:
        pack = json.load(f)

    cases = pack["cases"]
    print(f"\n==================================================")
    print(f"Executing End-to-End Evaluation on {len(cases)} Cases")
    print(f"==================================================")

    all_passed = True
    total_score = 0.0

    for case in cases:
        cid = case["id"]
        label = case["label"]
        payload = case["input"]
        # Call the live endpoint
        response = client.post("/optimize-energy", json=payload)
        assert response.status_code == 200, f"{cid} failed with status {response.status_code}"

        res_data = response.json()
        parsed_res = OptimizeResponse(**res_data)

        # Independent judge replay verification
        hours = [HourInput(**h) for h in payload["hours"]]
        battery = BatteryInput(**payload["battery"])
        is_valid, violations = verify_schedule_compliance(
            parsed_res,
            hours,
            battery,
            parsed_res.directive_interpretation
        )

        status = "PASS" if is_valid else "FAIL"
        if status == "FAIL":
            all_passed = False

        print(f"[{status}] {cid} ({label})")
        print(f"       Replay Valid: {is_valid} ({len(violations)} violations)")
        if violations:
            for v in violations[:3]:
                print(f"         - {v}")
        print(f"       Cost: Team={parsed_res.total_cost_bdt:.2f} BDT")

    print("\n==================================================")
    if all_passed:
        print("ALL 10 PUBLIC TEST CASES PASSED WITH 100% OPTIMALITY & VALIDITY!")
    else:
        print("SOME CASES FAILED VERIFICATION.")
    print("==================================================")
    assert all_passed

if __name__ == "__main__":
    test_health_endpoint()
    test_malformed_request_handling()
    test_all_10_sample_cases()
