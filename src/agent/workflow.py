from typing import Any, Dict
from agent.planner import SpatialPlanner, parse_parameters
from sandbox.executor import SpatialSandbox


class SpatialCopilotWorkflow:
    def __init__(self):
        self.planner = SpatialPlanner()
        self.sandbox = SpatialSandbox()

    def run(self, user_query: str) -> Dict[str, Any]:
        trace = [{"step": "planning", "message": "解析演示分析条件"}]
        try:
            plan = self.planner.plan(user_query)
            parameters = parse_parameters(user_query)
            trace.append({"step": "plan_generated", "operations": [op.description for op in plan.operations]})
            result = self.sandbox.execute_analysis(plan.operations, min_capacity=parameters.min_capacity)
            trace.append({"step": "execution_completed", "matched_count": result["matched_count"]})
            return {
                "status": "ok", "summary": f"演示分析命中 {result['matched_count']} 处设施。",
                "trace": trace, "geojson": result,
            }
        except ValueError:
            trace.append({"step": "validation_failed", "message": "分析参数无效"})
            return {"status": "error", "summary": "参数无效，未完成分析。", "trace": trace, "geojson": None}
        except Exception:
            trace.append({"step": "execution_error", "message": "空间计算失败"})
            return {"status": "error", "summary": "计算失败，未完成分析。", "trace": trace, "geojson": None}
