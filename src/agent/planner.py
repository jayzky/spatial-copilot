from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class SpatialOperation:
    step: int
    action_type: str
    target_layer: str
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class ExecutionPlan:
    query: str
    intent: str
    operations: List[SpatialOperation] = field(default_factory=list)


class SpatialPlanner:
    def plan(self, query: str) -> ExecutionPlan:
        q = query.lower()
        plan = ExecutionPlan(query=query, intent="spatial_analysis")

        distance = 500.0
        if "1000" in query or "1公里" in query or "1km" in q:
            distance = 1000.0
        elif "200" in query:
            distance = 200.0

        plan.operations.append(
            SpatialOperation(
                step=1,
                action_type="BUFFER",
                target_layer="waterlog_points",
                params={"distance": distance},
                description=f"针对积水点位生成 {distance} 米影响缓冲区",
            )
        )
        plan.operations.append(
            SpatialOperation(
                step=2,
                action_type="INTERSECT",
                target_layer="shelters",
                params={},
                description="空间拓扑求交，筛选缓冲区覆盖范围内的避难场所",
            )
        )
        return plan
