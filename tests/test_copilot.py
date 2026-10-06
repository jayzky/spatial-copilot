from agent.planner import SpatialPlanner
from agent.validator import QueryValidator
from agent.workflow import SpatialCopilotWorkflow


def test_spatial_planner():
    planner = SpatialPlanner()
    plan = planner.plan("查找积水点500米范围内的避难场所")
    assert len(plan.operations) == 2
    assert plan.operations[0].action_type == "BUFFER"
    assert plan.operations[0].params["distance"] == 500.0
    assert plan.operations[1].action_type == "INTERSECT"


def test_query_validator():
    validator = QueryValidator()
    ok, _ = validator.validate_sql("SELECT * FROM shelters WHERE ST_Intersects(geom, buffer);")
    assert ok is True

    bad, msg = validator.validate_sql("DROP TABLE shelters;")
    assert bad is False
    assert "安全拦截" in msg


def test_copilot_workflow_execution():
    wf = SpatialCopilotWorkflow()
    res = wf.run("筛选暴雨积水点周围500米内的应急避难所")
    assert res["geojson"]["type"] == "FeatureCollection"
    assert res["geojson"]["matched_count"] >= 1
    assert len(res["trace"]) >= 3
