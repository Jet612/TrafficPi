from pathlib import Path

from fastapi.testclient import TestClient

from trafficpi.api import openapi
from trafficpi.api.app import create_app
from trafficpi.api.settings import ApiSettings


def test_the_schema_describes_the_api_the_dashboard_uses() -> None:
    schema = openapi.build_schema()
    paths = schema["paths"]
    assert {"/api/status", "/api/stats/today", "/api/stats/series", "/api/events"} <= set(paths)
    assert {"/api/profile", "/api/settings", "/api/auth/login", "/api/auth/me"} <= set(paths)
    assert all(path.startswith("/api/") for path in paths)
    for name in ("StatusResponse", "TodaySummary", "EventOut", "SeriesPoint", "ProfileOut"):
        assert name in schema["components"]["schemas"]


def test_rendering_is_stable_text() -> None:
    first, second = openapi.render(openapi.build_schema()), openapi.render(openapi.build_schema())
    assert first == second and first.endswith("}\n")


def test_the_command_writes_the_file(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    assert openapi.main([str(target)]) == 0
    assert '"/api/status"' in target.read_text(encoding="utf-8")
    assert openapi.main([]) == 2


def test_the_schema_is_not_served_to_anyone(tmp_path: Path) -> None:
    app = create_app(ApiSettings(data_dir=tmp_path, dashboard_dir=None))
    with TestClient(app) as client:
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert client.get(path).status_code == 404
