import math
from functools import lru_cache
from typing import Any, Dict, List, Optional

from pyproj import CRS, Transformer
from shapely.geometry import Point, mapping
from shapely.ops import transform
from agent.validator import AnalysisParameters, AnalysisPoint


@lru_cache(maxsize=128)
def local_transformers(lon: float, lat: float):
    crs = CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    )
    return (
        Transformer.from_crs("EPSG:4326", crs, always_xy=True),
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True),
    )


class SpatialSandbox:
    """预定义空间算子执行器；不执行任意 Python 或 SQL。"""

    def __init__(self):
        points = [
            (114.401, 30.505, 0.55), (114.418, 30.492, 0.38),
            (114.412, 30.478, 0.42), (114.375, 30.485, 0.62),
            (114.368, 30.472, 0.28),
        ]
        shelters = [
            (114.358, 30.475, 3500), (114.388, 30.481, 2800),
            (114.415, 30.512, 6000), (114.403, 30.503, 1500),
            (114.425, 30.485, 1800), (114.420, 30.470, 2200),
            (114.432, 30.460, 4000),
        ]
        self.layers = {
            "waterlog_points": [
                {"id": i, "name": f"演示积水点 {i}", "geometry": Point(lon, lat),
                 "water_depth": depth, "severity": "模拟"}
                for i, (lon, lat, depth) in enumerate(points, 1)
            ],
            "shelters": [
                {"id": i, "name": f"演示设施 {i}", "geometry": Point(lon, lat),
                 "capacity": capacity, "district": "模拟片区", "phone": ""}
                for i, (lon, lat, capacity) in enumerate(shelters, 101)
            ],
        }

    @staticmethod
    def public_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **{k: v for k, v in item.items() if k != "geometry"},
            "geometry": mapping(item["geometry"]),
        }

    def demo_layers(self) -> Dict[str, Any]:
        return {
            "data_source": "synthetic_demo",
            "note": "自建演示点位与容量，不代表实时监测或实际设施服务状态",
            **{name: [self.public_item(item) for item in items]
               for name, items in self.layers.items()},
        }

    def add_custom_waterlog(self, name: str, lon: float, lat: float, depth: float = 0.3):
        point = AnalysisPoint(lon=lon, lat=lat)
        if isinstance(depth, bool) or not math.isfinite(depth) or depth < 0:
            raise ValueError("积水深度必须是有限非负数")
        item = {
            "id": max(p["id"] for p in self.layers["waterlog_points"]) + 1,
            "name": name, "geometry": Point(point.lon, point.lat),
            "water_depth": depth, "severity": "模拟",
        }
        self.layers["waterlog_points"].append(item)
        return self.public_item(item)

    def execute_analysis(
        self, operations: List[Any], custom_point: Optional[Dict[str, float]] = None,
        min_capacity: int = 0,
    ) -> Dict[str, Any]:
        buffers = [op for op in operations if getattr(op, "action_type", "") == "BUFFER"]
        if len(buffers) != 1 or any(
            getattr(op, "action_type", "") not in {"BUFFER", "INTERSECT"} for op in operations
        ):
            raise ValueError("必须包含一个 BUFFER，可选 INTERSECT；不支持其他算子")
        parameters = AnalysisParameters(
            distance_meters=buffers[0].params.get("distance", 500.0),
            min_capacity=min_capacity,
        )
        targets = list(self.layers["waterlog_points"])
        if custom_point is not None:
            point = AnalysisPoint.model_validate(custom_point)
            targets = [{
                "id": 999, "name": "自定义演示点", "geometry": Point(point.lon, point.lat),
                "water_depth": None, "severity": "未提供深度",
            }]
        if not targets:
            raise ValueError("至少需要一个分析点")
        features = []
        projected = []
        for item in targets:
            geometry = item["geometry"]
            forward, inverse = local_transformers(geometry.x, geometry.y)
            origin = Point(*forward.transform(geometry.x, geometry.y))
            projected.append((origin, forward))
            polygon = transform(
                inverse.transform, origin.buffer(parameters.distance_meters, quad_segs=128)
            )
            features.extend([
                {"type": "Feature", "geometry": mapping(geometry), "properties": {
                    "id": item["id"], "name": item["name"], "type": "waterlog",
                    "depth": item.get("water_depth"), "severity": item.get("severity", "模拟"),
                }},
                {"type": "Feature", "geometry": mapping(polygon), "properties": {
                    "type": "buffer_zone", "distance": parameters.distance_meters,
                    "target_point": item["name"],
                }},
            ])
        matched = []
        for item in self.layers["shelters"]:
            if item["capacity"] < parameters.min_capacity:
                continue
            geometry = item["geometry"]
            distance = min(
                origin.distance(Point(*forward.transform(geometry.x, geometry.y)))
                for origin, forward in projected
            )
            # 圆的边界包含；避免可视化多边形离散化误差排除边界附近点。
            if distance <= parameters.distance_meters + 1e-6:
                matched.append((distance, self.public_item(item)))
        matched.sort(key=lambda entry: (entry[0], str(entry[1]["id"])))
        shelters = [
            {**item, "distance_meters": round(distance, 3)}
            for distance, item in matched
        ]
        for item in shelters:
            features.append({
                "type": "Feature", "geometry": item["geometry"],
                "properties": {**{k: v for k, v in item.items() if k != "geometry"},
                               "type": "shelter"},
            })
        return {
            "type": "FeatureCollection", "features": features,
            "matched_count": len(shelters), "matched_shelters": shelters,
            "total_waterlog_count": len(targets),
            "parameters": parameters.model_dump(),
            "data_source": "synthetic_demo", "crs_name": "EPSG:4326",
            "distance_method": "WGS84 local azimuthal equidistant (meters)",
        }
