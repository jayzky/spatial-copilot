from dataclasses import dataclass, field
from typing import List, Dict, Any
import re
from agent.validator import AnalysisParameters


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"
DISTANCE = re.compile(rf"({NUMBER})\s*(公里|千米|km|米|m)(?![a-z])", re.I)
CAPACITY = re.compile(rf"({NUMBER})\s*人", re.I)


def parse_parameters(query: str) -> AnalysisParameters:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("查询内容不能为空")
    # 中文与数字相邻时没有单词边界，单位用于区分距离和容量。
    if re.search(r"(?:nan|[+-]?inf(?:inity)?)\s*(?:公里|米|km|m|人)", query, re.I):
        raise ValueError("分析参数必须是有限数值")
    distance_match = DISTANCE.search(query)
    distance = 500.0
    if distance_match:
        distance = float(distance_match[1])
        if distance_match[2].lower() in {"公里", "千米", "km"}:
            distance *= 1000
    capacity_match = CAPACITY.search(query)
    capacity = 0
    if capacity_match:
        raw = float(capacity_match[1])
        if not raw.is_integer():
            raise ValueError("容量必须是非负整数")
        capacity = int(raw)
        if capacity < 0:
            raise ValueError("容量必须是非负整数")
        prefix = query[:capacity_match.start()].rstrip()
        modifier = re.search(
            r"(大于等于|小于等于|不小于|不低于|不少于|不大于|不高于|不多于|不超过|至少|至多|最多|超过|大于|高于|多于|小于|低于|少于|不到|恰好|等于|>=|<=|>|<|≥|≤|=)\s*$",
            prefix,
        )
        relation = modifier[1] if modifier else "至少"
        if relation in {"小于等于", "不大于", "不高于", "不多于", "不超过", "至多", "最多", "小于", "低于", "少于", "不到", "恰好", "等于", "<=", "<", "≤", "="}:
            raise ValueError("当前仅支持最低容量条件，不支持容量上界或等于条件")
        if relation in {"超过", "大于", "高于", "多于", ">"}:
            capacity += 1
    return AnalysisParameters(distance_meters=distance, min_capacity=capacity)


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
        parameters = parse_parameters(query)
        plan = ExecutionPlan(query=query, intent="spatial_analysis")
        distance = parameters.distance_meters

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
