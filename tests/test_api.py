import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pyproj import Geod
from shapely.geometry import Point

import api.server as server
from agent.llm_agent import RealLLMSpatialAgent, SPATIAL_TOOLS
from agent.planner import SpatialPlanner, parse_parameters
from sandbox.executor import SpatialSandbox


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def operation(distance=500.0):
    return SimpleNamespace(action_type="BUFFER", params={"distance": distance})


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(frame[6:]) for frame in response.text.strip().split("\n\n")]


def collect(agent, **kwargs):
    async def run():
        return [item async for item in agent.run_stream(**kwargs)]
    return asyncio.run(run())


def tool_response(arguments=None, tool="buffer_analysis"):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
        "id": "test-call", "type": "function", "function": {
            "name": tool, "arguments": arguments if arguments is not None else '{"distance_meters":1000,"min_capacity":1500}',
        },
    }]}}]}


def test_result_is_finite_json_and_demo_metadata():
    sandbox = SpatialSandbox()
    result = sandbox.execute_analysis([operation()])
    json.dumps(result, allow_nan=False)
    json.dumps(sandbox.demo_layers(), allow_nan=False)
    assert result["matched_count"] == len(result["matched_shelters"])
    assert result["data_source"] == "synthetic_demo"
    assert all(isinstance(item["geometry"], dict) for item in result["matched_shelters"])
    assert all(not item["phone"] for item in result["matched_shelters"])


def test_metric_selection_includes_boundary_and_excludes_outside():
    geod = Geod(ellps="WGS84")
    center = {"lon": 114.4, "lat": 30.5}
    sandbox = SpatialSandbox()
    sandbox.layers["shelters"] = []
    for ident, distance in enumerate([499.9, 500.0, 500.1], 1):
        lon, lat, _ = geod.fwd(center["lon"], center["lat"], 37, distance)
        sandbox.layers["shelters"].append({
            "id": ident, "name": str(ident), "geometry": Point(lon, lat), "capacity": 100,
        })
    result = sandbox.execute_analysis([operation()], custom_point=center)
    assert [s["id"] for s in result["matched_shelters"]] == [1, 2]
    assert result["matched_shelters"][0]["distance_meters"] == pytest.approx(499.9, abs=0.002)
    assert result["matched_shelters"][1]["distance_meters"] == pytest.approx(500.0, abs=0.002)


def test_multiple_centers_do_not_duplicate_facilities():
    sandbox = SpatialSandbox()
    result = sandbox.execute_analysis([operation(50000.0)])
    ids = [s["id"] for s in result["matched_shelters"]]
    assert len(ids) == len(set(ids)) == 7
    assert [s["distance_meters"] for s in result["matched_shelters"]] == sorted(s["distance_meters"] for s in result["matched_shelters"])


@pytest.mark.parametrize("distance", [0, -1, float("nan"), float("inf"), True, "500", 50001])
def test_invalid_radius_rejected(distance):
    with pytest.raises(ValueError):
        SpatialSandbox().execute_analysis([operation(distance)])


@pytest.mark.parametrize("capacity", [-1, 1.5, True, "20", float("nan")])
def test_invalid_capacity_rejected(capacity):
    with pytest.raises(ValueError):
        SpatialSandbox().execute_analysis([operation()], min_capacity=capacity)


@pytest.mark.parametrize("query,radius,capacity", [
    ("750米，至少3000人", 750, 3000), ("1.5公里，超过2000人", 1500, 2001),
    ("2000米范围", 2000, 0), ("30000人", 500, 30000),
    ("500米内容量大于2000人", 500, 2001),
    ("500米内至少2000人", 500, 2000),
    ("500米内不低于2000人", 500, 2000),
    ("500米内不少于2000人", 500, 2000),
    ("500米内不小于2000人", 500, 2000),
    ("500米内容量大于等于2000人", 500, 2000),
    ("500米内容量>=2000人", 500, 2000),
])
def test_query_units_are_not_confused_with_capacity(query, radius, capacity):
    params = parse_parameters(query)
    assert params.distance_meters == radius
    assert params.min_capacity == capacity
    assert SpatialPlanner().plan(query).operations[0].params["distance"] == radius


@pytest.mark.parametrize("query", ["-1米", "NaN米", "1e309米", "-2人", "2.5人", "   ", "容量小于2000人", "容量不超过2000人", "容量至多2000人", "超过-1人", "不大于2000人", "不高于2000人", "不多于2000人"])
def test_invalid_query_parameters_rejected(query):
    with pytest.raises(ValueError):
        parse_parameters(query)


def test_local_sse_finishes_and_static_assets_work():
    with TestClient(server.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/vendor/leaflet.js").status_code == 200
        assert client.get("/api/demo").json()["data_source"] == "synthetic_demo"
        output = events(client.post("/api/query/stream", json={"query": "500米", "mode": "local"}))
    assert [e["event"] for e in output] == ["mode", "thought", "tool_call", "tool_result", "result"]
    result = output[-1]["data"]["geojson"]
    assert result["matched_count"] > 0
    assert output[-1]["data"]["mode"] == "local"
    json.dumps(output, allow_nan=False)


def test_explicit_overrides_and_no_match():
    with TestClient(server.app) as client:
        output = events(client.post("/api/query/stream", json={
            "query": "500米", "distance_meters": 1000, "min_capacity": 100000,
        }))
    result = output[-1]["data"]["geojson"]
    assert result["parameters"] == {"distance_meters": 1000.0, "min_capacity": 100000}
    assert result["matched_shelters"] == []


@pytest.mark.parametrize("payload", [
    {"query": ""}, {"query": "   "}, {"query": "500米", "mode": "invalid"},
    {"query": "500米", "distance_meters": -1},
    {"query": "500米", "min_capacity": True},
    {"query": "500米", "custom_point": {"lon": 200, "lat": 30}},
    {"query": "500米", "custom_point": {"lon": 116, "lat": 30}},
    {"query": "500米", "custom_point": {"lon": 114.4, "lat": 200}},
])
def test_api_invalid_input_is_422(payload):
    with TestClient(server.app) as client:
        response = client.post("/api/query/stream", json=payload)
    assert response.status_code == 422


def test_nan_validation_does_not_echo_secret_or_crash():
    with TestClient(server.app) as client:
        response = client.post("/api/query/stream", content='{"query":"500米","distance_meters":NaN,"api_key":"private-test"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "private-test" not in response.text
    assert "NaN" not in response.text


def test_remote_mode_requires_key_without_silent_fallback():
    output = collect(RealLLMSpatialAgent(), query="500米", mode="remote")
    assert output[-1]["data"]["code"] == "missing_api_key"
    assert not any(e["event"] == "result" for e in output)
    assert [t["function"]["name"] for t in SPATIAL_TOOLS] == ["buffer_analysis"]


def test_mock_remote_tool_roundtrip():
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json=tool_response() if len(requests) == 1 else {"choices": [{"message": {"content": "样例分析完成"}}]})
    output = collect(RealLLMSpatialAgent(httpx.MockTransport(handler)), query="1000米 至少1500人", mode="remote", api_key="offline-test", base_url="https://model.test/v1")
    assert output[-1]["event"] == "result"
    assert len(requests) == 2
    assert requests[1]["messages"][-1]["role"] == "tool"
    tool = json.loads(requests[1]["messages"][-1]["content"])
    assert tool["parameters"]["distance_meters"] == 1000
    assert all(s["capacity"] >= 1500 for s in tool["matched_shelters"])
    assert "offline-test" not in json.dumps(output)


@pytest.mark.parametrize("first,expected", [
    ({"choices": []}, "invalid_model_response"),
    ({"choices": [{"message": {"content": "虚构总结"}}]}, "missing_tool_call"),
    (tool_response(tool="execute_spatial_sql"), "unsupported_tool"),
    (tool_response(arguments="not-json"), "invalid_model_response"),
    (tool_response(arguments="{}"), "invalid_model_response"),
    (tool_response(arguments='{"distance_meters":-1}'), "invalid_model_response"),
    (tool_response(arguments='{"distance_meters":500,"min_capacity":true}'), "invalid_model_response"),
])
def test_malformed_model_output_never_reports_success(first, expected):
    agent = RealLLMSpatialAgent(httpx.MockTransport(lambda request: httpx.Response(200, json=first)))
    output = collect(agent, query="500米", mode="remote", api_key="offline-test")
    assert output[-1]["data"]["code"] == expected
    assert not any(e["event"] == "result" for e in output)


@pytest.mark.parametrize("failed_call", [1, 2])
def test_upstream_http_failure_is_terminal(failed_call):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="private-test") if len(calls) == failed_call else httpx.Response(200, json=tool_response())
    output = collect(RealLLMSpatialAgent(httpx.MockTransport(handler)), query="500米", mode="remote", api_key="private-test")
    assert output[-1]["data"]["code"] == "upstream_http"
    assert not any(e["event"] == "result" for e in output)
    assert "private-test" not in json.dumps(output)


def test_timeout_is_terminal():
    def handler(request):
        raise httpx.ReadTimeout("private-test", request=request)
    output = collect(RealLLMSpatialAgent(httpx.MockTransport(handler)), query="500米", mode="remote", api_key="offline-test")
    assert output[-1]["data"]["code"] == "upstream_timeout"
    assert "private-test" not in json.dumps(output)


def test_server_key_cannot_be_forwarded_to_custom_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-private-test")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=tool_response())
    output = collect(RealLLMSpatialAgent(httpx.MockTransport(handler)), query="500米", mode="remote", base_url="https://untrusted.test/v1")
    assert requests == []
    assert output[-1]["data"]["code"] == "server_key_endpoint_mismatch"
    assert "server-private-test" not in json.dumps(output)


def test_tool_exception_is_error(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("private-test")
    agent = RealLLMSpatialAgent()
    monkeypatch.setattr(agent.sandbox, "execute_analysis", broken)
    output = collect(agent, query="500米", mode="local")
    assert output[-1]["data"]["code"] == "analysis_failed"
    assert not any(e["event"] == "result" for e in output)


def test_stream_exception_is_error_and_does_not_echo_details(monkeypatch):
    async def broken(**kwargs):
        yield {"event": "mode", "data": {"mode": "local"}}
        raise RuntimeError("private-test")
    monkeypatch.setattr(server.agent, "run_stream", broken)
    with TestClient(server.app) as client:
        output = events(client.post("/api/query/stream", json={"query": "500米"}))
    assert output[-1]["data"]["code"] == "stream_failed"
    assert "private-test" not in json.dumps(output)
