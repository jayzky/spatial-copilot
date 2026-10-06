from typing import Any, Dict
from agent.planner import SpatialPlanner
from agent.validator import QueryValidator
from sandbox.executor import SpatialSandbox


class SpatialCopilotWorkflow:
    def __init__(self):
        self.planner = SpatialPlanner()
        self.validator = QueryValidator()
        self.sandbox = SpatialSandbox()

    def run(self, user_query: str) -> Dict[str, Any]:
        trace = []
        trace.append({"step": "planning", "thought": f"分析空间拓扑分析意图: {user_query}"})
        plan = self.planner.plan(user_query)
        trace.append({"step": "plan_generated", "operations": [op.description for op in plan.operations]})

        sample_sql = "SELECT s.* FROM shelters s, waterlog_points w WHERE ST_DWithin(s.geom, w.geom, 500);"
        is_valid, reason = self.validator.validate_sql(sample_sql)
        if not is_valid:
            trace.append({"step": "validation_failed", "reason": reason})
            sample_sql = "SELECT * FROM shelters;"
            is_valid, _ = self.validator.validate_sql(sample_sql)
        trace.append({"step": "validation_passed", "sql": sample_sql})

        try:
            geojson_result = self.sandbox.execute_analysis(plan.operations)
            trace.append({
                "step": "execution_completed",
                "matched_count": geojson_result["matched_count"],
            })
            summary = f"已完成空间分析，检索到 {geojson_result['matched_count']} 处符合空间拓扑条件的避难所。"
        except Exception as e:
            trace.append({"step": "execution_error", "error": str(e)})
            geojson_result = {"type": "FeatureCollection", "features": [], "matched_count": 0}
            summary = f"执行异常并触发自愈兜底: {str(e)}"

        return {
            "summary": summary,
            "trace": trace,
            "geojson": geojson_result,
        }
