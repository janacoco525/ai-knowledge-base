import sys
import os

project_root = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, os.path.abspath(project_root))

from app.rag_app.api_server import app
from fastapi.testclient import TestClient


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "apiKeyPresent" in data


def test_providers_endpoint():
    response = client.get("/api/providers")
    assert response.status_code == 200
    data = response.json()
    assert "providers" in data
    assert isinstance(data["providers"], list)


def test_active_provider_endpoint():
    response = client.get("/api/providers/active")
    assert response.status_code == 200
    data = response.json()
    assert "activeProvider" in data or "providerId" in data
