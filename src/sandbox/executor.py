from typing import Any, Dict, List
from shapely.geometry import Point, Polygon, mapping


class SpatialSandbox:
    def __init__(self):
        self.layers: Dict[str, List[Dict[str, Any]]] = {
            "waterlog_points": [
                {"id": 1, "name": "洪山区光谷广场积水点", "geometry": Point(114.40, 30.50), "water_depth": 0.45},
                {"id": 2, "name": "关山大道积水点", "geometry": Point(114.42, 30.49), "water_depth": 0.32},
            ],
            "shelters": [
                {"id": 101, "name": "华中农业大学体育馆避难所", "geometry": Point(114.36, 30.47), "capacity": 3000},
                {"id": 102, "name": "光谷一小应急避难所", "geometry": Point(114.402, 30.501), "capacity": 1500},
                {"id": 103, "name": "中南财经政法大学礼堂", "geometry": Point(114.39, 30.48), "capacity": 2000},
            ],
        }

    def execute_analysis(self, plan_operations: List[Any]) -> Dict[str, Any]:
        meter_to_deg = 1.0 / 111319.0
        buffer_polygons: List[Polygon] = []
        distance_m = 500.0

        for op in plan_operations:
            if op.action_type == "BUFFER":
                distance_m = float(op.params.get("distance", 500.0))
                dist_deg = distance_m * meter_to_deg
                for pt in self.layers["waterlog_points"]:
                    buf = pt["geometry"].buffer(dist_deg)
                    buffer_polygons.append(buf)

        matched_shelters = []
        if buffer_polygons:
            for shelter in self.layers["shelters"]:
                geom = shelter["geometry"]
                for buf in buffer_polygons:
                    if buf.intersects(geom):
                        matched_shelters.append(shelter)
                        break
        else:
            matched_shelters = self.layers["shelters"]

        features = []
        for p in self.layers["waterlog_points"]:
            features.append({
                "type": "Feature",
                "geometry": mapping(p["geometry"]),
                "properties": {"name": p["name"], "type": "waterlog", "depth": p["water_depth"]}
            })
        for b in buffer_polygons:
            features.append({
                "type": "Feature",
                "geometry": mapping(b),
                "properties": {"type": "buffer_zone", "distance": distance_m}
            })
        for s in matched_shelters:
            features.append({
                "type": "Feature",
                "geometry": mapping(s["geometry"]),
                "properties": {"name": s["name"], "type": "shelter", "capacity": s["capacity"]}
            })

        return {
            "type": "FeatureCollection",
            "features": features,
            "matched_count": len(matched_shelters),
        }
