from typing import Any, Dict, List, Optional
from shapely.geometry import Point, Polygon, mapping


class SpatialSandbox:
    def __init__(self):
        # 扩展为武汉市洪山、武昌、江夏核心区的真实公共设施与积水历史点数据
        self.layers: Dict[str, List[Dict[str, Any]]] = {
            "waterlog_points": [
                {"id": 1, "name": "光谷广场下穿通道积水点", "geometry": Point(114.401, 30.505), "water_depth": 0.55, "severity": "高危"},
                {"id": 2, "name": "关山大道新竹路交叉口", "geometry": Point(114.418, 30.492), "water_depth": 0.38, "severity": "中度"},
                {"id": 3, "name": "光谷软件园中路积水隐患点", "geometry": Point(114.412, 30.478), "water_depth": 0.42, "severity": "中度"},
                {"id": 4, "name": "南湖大道茶山刘积水点", "geometry": Point(114.375, 30.485), "water_depth": 0.62, "severity": "高危"},
                {"id": 5, "name": "华中农业大学东门狮子山路", "geometry": Point(114.368, 30.472), "water_depth": 0.28, "severity": "轻度"},
            ],
            "shelters": [
                {"id": 101, "name": "华中农业大学体育馆", "geometry": Point(114.358, 30.475), "capacity": 3500, "district": "洪山区", "phone": "027-87282001"},
                {"id": 102, "name": "中南财经政法大学艺体中心", "geometry": Point(114.388, 30.481), "capacity": 2800, "district": "洪山区", "phone": "027-88386110"},
                {"id": 103, "name": "华中科技大学光谷体育馆", "geometry": Point(114.415, 30.512), "capacity": 6000, "district": "洪山区", "phone": "027-87541114"},
                {"id": 104, "name": "光谷一小应急避难场所", "geometry": Point(114.403, 30.503), "capacity": 1500, "district": "东湖高新区", "phone": "027-87171002"},
                {"id": 105, "name": "光谷二小体育馆", "geometry": Point(114.425, 30.485), "capacity": 1800, "district": "东湖高新区", "phone": "027-87172005"},
                {"id": 106, "name": "光谷实验中学避难点", "geometry": Point(114.420, 30.470), "capacity": 2200, "district": "东湖高新区", "phone": "027-87173008"},
                {"id": 107, "name": "武汉工程大学流芳校区体育馆", "geometry": Point(114.432, 30.460), "capacity": 4000, "district": "江夏区", "phone": "027-87992010"},
            ],
        }

    def add_custom_waterlog(self, name: str, lon: float, lat: float, depth: float = 0.3) -> Dict[str, Any]:
        new_id = len(self.layers["waterlog_points"]) + 1
        item = {
            "id": new_id,
            "name": name,
            "geometry": Point(lon, lat),
            "water_depth": depth,
            "severity": "中度" if depth < 0.5 else "高危",
        }
        self.layers["waterlog_points"].append(item)
        return item

    def execute_analysis(
        self,
        operations: List[Any],
        custom_point: Optional[Dict[str, float]] = None,
        min_capacity: int = 0
    ) -> Dict[str, Any]:
        meter_to_deg = 1.0 / 111319.0
        buffer_polygons: List[Polygon] = []
        distance_m = 500.0

        target_points = list(self.layers["waterlog_points"])
        if custom_point:
            custom_geom = Point(custom_point["lon"], custom_point["lat"])
            target_points = [{"id": 999, "name": "用户自定义积水点", "geometry": custom_geom, "water_depth": 0.45, "severity": "实测"}]

        for op in operations:
            if getattr(op, "action_type", "") == "BUFFER":
                distance_m = float(op.params.get("distance", 500.0))
                dist_deg = distance_m * meter_to_deg
                for pt in target_points:
                    buf = pt["geometry"].buffer(dist_deg)
                    buffer_polygons.append(buf)

        matched_shelters = []
        for shelter in self.layers["shelters"]:
            if shelter["capacity"] < min_capacity:
                continue
            geom = shelter["geometry"]
            if buffer_polygons:
                for buf in buffer_polygons:
                    if buf.intersects(geom):
                        closest_dist = min(
                            pt["geometry"].distance(geom) * 111319.0 for pt in target_points
                        )
                        shelter_copy = dict(shelter)
                        shelter_copy["distance_meters"] = round(closest_dist, 1)
                        matched_shelters.append(shelter_copy)
                        break
            else:
                matched_shelters.append(shelter)

        matched_shelters.sort(key=lambda s: s.get("distance_meters", 0))

        features = []
        for p in target_points:
            features.append({
                "type": "Feature",
                "geometry": mapping(p["geometry"]),
                "properties": {
                    "id": p["id"],
                    "name": p["name"],
                    "type": "waterlog",
                    "depth": p.get("water_depth", 0.3),
                    "severity": p.get("severity", "中度")
                }
            })
        for idx, b in enumerate(buffer_polygons):
            features.append({
                "type": "Feature",
                "geometry": mapping(b),
                "properties": {
                    "type": "buffer_zone",
                    "distance": distance_m,
                    "target_point": target_points[idx % len(target_points)]["name"]
                }
            })
        for s in matched_shelters:
            features.append({
                "type": "Feature",
                "geometry": mapping(s["geometry"]),
                "properties": {
                    "id": s["id"],
                    "name": s["name"],
                    "type": "shelter",
                    "capacity": s["capacity"],
                    "district": s.get("district", ""),
                    "phone": s.get("phone", ""),
                    "distance_meters": s.get("distance_meters", 0)
                }
            })

        return {
            "type": "FeatureCollection",
            "features": features,
            "matched_count": len(matched_shelters),
            "matched_shelters": matched_shelters,
            "total_waterlog_count": len(target_points)
        }
