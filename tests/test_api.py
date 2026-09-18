import json
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_optimize_endpoint_with_sample_request():
    with open("sample_request.json", "r") as f:
        payload = json.load(f)

    response = client.post("/optimize", json=payload)
    assert response.status_code == 200
    data = response.json()
    
    assert data["scenario_id"] == "GRID-101"
    assert len(data["directive_interpretation"]) == len(payload["operator_notes"])
    assert len(data["hourly_plan"]) == 24
    assert data["total_grid_kwh"] > 0
    assert data["total_cost_bdt"] > 0
    assert "plan_summary" in data

def test_optimize_endpoint_invalid_input():
    with open("sample_request.json", "r") as f:
        payload = json.load(f)

    # Corrupt demand to negative value
    payload["hours"][0]["demand_kwh"] = -50.0
    response = client.post("/optimize", json=payload)
    assert response.status_code in [400, 422]
